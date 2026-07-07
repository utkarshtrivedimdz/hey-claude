"""Runtime: wire wake → state machine on a CFRunLoop (see ARCHITECTURE §1, §3).

All state mutations happen on the main run-loop: an NSTimer drains wake events
(produced on the wake thread) and calls tick(); the AXObserver delivers box
changes. The wake thread only enqueues, so the state machine is single-threaded.
"""
from __future__ import annotations

import argparse
import logging
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from . import __version__, config as config_mod, log as logmod
from .commands import from_config, fixups_from_config
from .state import StateMachine, S
from .telemetry import Telemetry

log = logging.getLogger(__name__)

_SOUNDS = {
    "armed": "Tink", "send": "Pop", "cancel": "Bottle",
    "stop": "Funk", "error": "Basso",
    "listening": "Glass",   # dictation verified ON (button turned blue)
    "dropped": "Sosumi",    # dictation turned OFF externally mid-turn (mic clicked off)
    "deaf": "Submarine",    # mic device disconnected → not-listening (click menu bar to retry)
    "dialog": "Ping",       # a Claude approval/choice box appeared (overridden by cfg at startup)
}


def _beep(kind: str) -> None:
    name = _SOUNDS.get(kind)
    if name:
        subprocess.Popen(
            ["afplay", f"/System/Library/Sounds/{name}.aiff"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def _find_config(explicit: str | None) -> str | None:
    for c in (explicit, os.environ.get("HEY_CLAUDE_CONFIG"),
              str(Path("~/.config/hey-claude/config.toml").expanduser()),
              str(Path(__file__).resolve().parent.parent / "config.toml")):
        if c and Path(c).expanduser().exists():
            return c
    return None


def _build(cfg):
    from .ax import RealAX
    from .keys import RealKeys
    from .system import RealSystem, AppFocusObserver
    from .bootstrap import Bootstrap
    from .appstate import AppStateMachine, AppState
    from .reconcile import Reconciler

    system = RealSystem(cfg)
    ax = RealAX(cfg)
    keys = RealKeys(cfg.keymap)
    boot = Bootstrap(system, cfg)
    tel = Telemetry(cfg)
    appstate = AppStateMachine()  # the foreground gate (§3a)
    sm = StateMachine(cfg, boot, keys, ax, from_config(cfg), tel, beep=_beep,
                      fixups=fixups_from_config(cfg), appstate=appstate)
    reconciler = Reconciler(cfg, system, ax, sm, appstate)
    focus = AppFocusObserver(cfg, system, appstate)

    # Foreground is the correctness gate: the dialog observer is only trustworthy while VS Code is
    # frontmost, so attach it on →FOREGROUND and detach (freeze) on →BACKGROUND/→NOT_RUNNING. A
    # →FOREGROUND after a relaunch re-attaches to the NEW pid (loophole #3); find_dialog/observe
    # re-assert AXManualAccessibility on the fresh element themselves.
    def _on_appstate_change(old, new):
        if not cfg.dialog_enabled:
            return
        if new is AppState.FOREGROUND:
            log.info("VS Code FOREGROUND → attach dialog observer + refresh tabs")
            ax.observe_dialog(sm.on_dialog_change)
            reconciler.tabs = system.list_tabs()
        else:
            log.info("VS Code %s → detach dialog observer (freeze dialog state)", new.value)
            ax.stop_observing_dialog()
    appstate.set_on_change(_on_appstate_change)

    ax.set_manual_a11y()  # unlock the a11y tree at startup (per-process, resets on VS Code relaunch)
    return sm, ax, focus, reconciler


def run(cfg, once: bool = False) -> int:
    from Foundation import NSTimer
    from CoreFoundation import CFRunLoopGetCurrent, CFRunLoopStop, CFRunLoopRun

    _SOUNDS["dialog"] = cfg.dialog_announce_sound  # honor [dialog] announce_sound
    sm, ax, focus, reconciler = _build(cfg)
    q: "queue.Queue[float]" = queue.Queue()
    # use_nsapp: run under NSApplication (menu bar toggle) rather than a bare run loop.
    # Only the daemon needs it; --once stays on CFRunLoopRun so its tested smoke path
    # is byte-for-byte unchanged. `listening` is the menu-bar mute state (see _toggle).
    use_nsapp = (not once) and cfg.menubar_enabled
    state = {"armed_once": False, "mic_lost": False, "listening": True, "use_nsapp": use_nsapp}

    def _stop_runloop():
        # NSApp.terminate_ exits cleanly (code 0); CFRunLoopStop ends the bare loop.
        if state["use_nsapp"]:
            from AppKit import NSApplication
            NSApplication.sharedApplication().terminate_(None)
        else:
            CFRunLoopStop(CFRunLoopGetCurrent())

    # Wake-thread lifecycle. The listener runs on its own thread and only enqueues scores
    # (invariant #4 — the state machine still mutates only on the main loop). There is only
    # ever one wake thread; the menu-bar toggle stop/starts it so "off" closes the
    # InputStream and macOS releases the mic. `once` arms without a wake thread at all.
    from .wake import WakeListener
    wake = {"thread": None, "listener": None}
    # The menu bar is created later and only when use_nsapp; hold it here so tick() (already
    # scheduled by then) can repaint the icon on a mic drop without capturing a name that
    # doesn't exist yet.
    ui = {"menubar": None}

    def _on_mic_lost():  # called from the wake thread; tick() (main thread) acts on it
        state["mic_lost"] = True

    def _start_wake():
        old = wake["thread"]
        if old is not None and old.is_alive():
            old.join(timeout=1.0)  # let the previous stream fully close before reopening
        listener = WakeListener(cfg, lambda score: q.put(score), on_mic_lost=_on_mic_lost)
        t = threading.Thread(target=listener.run, name="wake", daemon=True)
        wake["listener"], wake["thread"] = listener, t
        t.start()

    def _stop_wake():
        listener = wake["listener"]
        if listener is not None:
            listener.stop()  # thread exits within ~0.5s and closes the InputStream (mic released)
        wake["listener"] = None

    def _wake_alive() -> bool:
        t = wake["thread"]
        return t is not None and t.is_alive()

    def tick(_timer):
        # Mic device disconnected (Bluetooth drop) → the wake thread has already exited, so
        # we're deaf. With a menu bar we stay up and just repaint the icon to not-listening;
        # a click self-heals via _toggle (restarts the listener + re-inits PortAudio). There's
        # nothing to auto-recover to while the mic is gone, so we don't respawn blindly.
        # Without a menu bar there's no click-to-heal path, so fall back to a clean exit —
        # plist KeepAlive={SuccessfulExit: false} leaves it down for a manual reconnect + restart.
        if state["mic_lost"]:
            mb = ui["menubar"]
            if mb is not None:
                state["mic_lost"] = False  # handle once; the wake thread has already exited
                log.critical("mic lost — staying up, marked not-listening (click menu bar to retry)")
                _beep("deaf")
                mb.set_listening(False)
                # fall through: sm.tick() keeps reconciling in-flight turns; no wake events
                # arrive until a click restarts the listener.
            else:
                log.critical("mic lost — stopping daemon (no menu bar to self-heal; reconnect mic + restart)")
                _beep("deaf")
                _stop_runloop()
                return
        drained = 0
        while True:
            try:
                score = q.get_nowait()
            except queue.Empty:
                break
            drained += 1
            if state["listening"]:
                sm.on_wake(score)
            # else: muted from the menu bar — discard any score still in flight so a late
            # wake never fires after toggle-off.
        if drained:
            log.debug("tick: drained %d wake event(s), state=%s listening=%s",
                      drained, sm.state.value, state["listening"])
        sm.tick()
        if once:
            if sm.state is not S.IDLE:
                state["armed_once"] = True
            elif state["armed_once"]:
                log.info("--once: turn complete, stopping run loop")
                _stop_runloop()

    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.12, True, tick)

    # Dialog sensing (Phase 1): the AppFocusObserver seeds AppState from current reality and drives
    # attach/detach of the dialog observer (via the _build on_change hook); the reconciler timer is
    # the foreground-gated backstop sweep (§5) that snaps the FSMs to UI truth if an event dropped.
    if cfg.dialog_enabled:
        focus.start()
        interval = cfg.dialog_reconcile_interval_s
        if interval > 0:
            NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                interval, True, lambda _t: reconciler.tick())
            log.info("dialog reconciler armed: every %.1fs (foreground-gated)", interval)

    if once:
        log.info("--once: arming now (no wake word) — dictate a prompt then say your command word")
        print("hey-claude --once: arming now — dictate a prompt then say your command word.")
        q.put(1.0)
        # CFRunLoopRun() (not NSRunLoop.run()) so CFRunLoopStop cleanly ends --once.
        # The AXObserver source and NSTimer both live on this same run loop.
        try:
            CFRunLoopRun()
        except KeyboardInterrupt:
            log.info("interrupted — shutting down")
        return 0

    _start_wake()
    log.info("hey-claude listening: wake=%r model=%r device=%s",
             cfg.wake_phrase, cfg.wake_model or cfg.wake_pretrained_fallback, cfg.mic_device)
    print(f"hey-claude listening (wake='{cfg.wake_phrase}', "
          f"model='{cfg.wake_model or cfg.wake_pretrained_fallback}'). Ctrl+C to quit.")

    if use_nsapp:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        from .menubar import MenuBar

        menubar = MenuBar()
        ui["menubar"] = menubar  # let tick() repaint the icon on a mic drop

        def _toggle():
            # Self-heal: the wake thread can die without the process exiting (PortAudio
            # crash on a device change). A click while we still believe we're listening
            # restarts the listener — which re-inits PortAudio (see wake.py) and reopens
            # the mic — instead of muting a thread that's already dead.
            if state["listening"] and not _wake_alive():
                log.warning("menu bar: click while deaf (wake thread dead) — restarting wake listener")
                _start_wake()
                menubar.set_listening(True)
                return
            state["listening"] = not state["listening"]
            menubar.set_listening(state["listening"])  # repaint first — start/stop can briefly block
            if state["listening"]:
                log.info("menu bar: listening ON — starting wake listener")
                _start_wake()
            else:
                log.info("menu bar: listening OFF — releasing mic")
                _stop_wake()

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # menu bar item, no Dock icon
        menubar.attach(_toggle)
        try:
            app.run()
        except KeyboardInterrupt:
            log.info("interrupted — shutting down")
        return 0

    # menubar disabled → original bare run loop (AXObserver + NSTimer live here too)
    try:
        CFRunLoopRun()
    except KeyboardInterrupt:
        log.info("interrupted — shutting down")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="hey-claude", description="Wake-word voice controller for Claude Code")
    p.add_argument("--config", help="path to config.toml")
    p.add_argument("--once", action="store_true", help="arm once without the wake word (M1 smoke test)")
    p.add_argument("--read", action="store_true", help="print the Claude box AXValue once and exit (debug)")
    p.add_argument("--mic-check", action="store_true", help="record 2s and report level (verify Mic permission)")
    p.add_argument("--debug", action="store_true", help="verbose DEBUG logging to stderr + ~/Library/Logs/hey-claude/hey-claude.log")
    p.add_argument("--version", action="store_true")
    args = p.parse_args(argv)

    if args.version:
        print(f"hey-claude {__version__}")
        return 0

    if args.mic_check:
        import numpy as np
        import sounddevice as sd
        print("input device:", sd.query_devices(kind="input")["name"])
        print("recording 2s — speak now…")
        rec = sd.rec(int(2 * 16000), samplerate=16000, channels=1, dtype="int16")
        sd.wait()
        peak = int(np.abs(rec.astype("int32")).max())
        ok = peak > 300
        print(f"peak amplitude: {peak} / 32767 — {'MIC OK' if ok else 'SILENT (grant Microphone to this process)'}")
        return 0 if ok else 2

    cfg_path = _find_config(args.config)
    cfg = config_mod.load(cfg_path)
    logmod.configure(debug=logmod.debug_enabled(args.debug), log_dir=cfg.telemetry_log_dir)
    log.info("hey-claude %s starting (config=%s, debug=%s)", __version__, cfg_path, logmod.debug_enabled(args.debug))

    if args.read:
        from .ax import RealAX
        ax = RealAX(cfg.target_bundle_id)
        ax.set_manual_a11y()
        print(repr(ax.read_box()))
        return 0

    return run(cfg, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
