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
    "deaf": "Submarine",    # mic device disconnected → daemon stopping (manual restart)
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
    from .system import RealSystem
    from .bootstrap import Bootstrap

    system = RealSystem(cfg)
    ax = RealAX(cfg)
    keys = RealKeys(cfg.keymap)
    boot = Bootstrap(system, cfg)
    tel = Telemetry(cfg)
    sm = StateMachine(cfg, boot, keys, ax, from_config(cfg), tel, beep=_beep,
                      fixups=fixups_from_config(cfg))
    ax.set_manual_a11y()  # unlock the a11y tree at startup (per-process, resets on VS Code relaunch)
    return sm, ax


def run(cfg, once: bool = False) -> int:
    from Foundation import NSTimer
    from CoreFoundation import CFRunLoopGetCurrent, CFRunLoopStop, CFRunLoopRun

    sm, ax = _build(cfg)
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

    def tick(_timer):
        # Mic device disconnected (Bluetooth drop) → stop cleanly rather than sit deaf.
        # Clean exit(0) + plist KeepAlive={SuccessfulExit: false} means launchd leaves it
        # down; recovery is manual (reconnect mic + restart). A real crash still respawns.
        if state["mic_lost"]:
            log.critical("mic lost — stopping daemon (clean exit; reconnect mic + restart to resume)")
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

        def _toggle():
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
