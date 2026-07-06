"""Runtime: wire wake → state machine on a CFRunLoop (see ARCHITECTURE §1, §3).

All state mutations happen on the main run-loop: an NSTimer drains wake events
(produced on the wake thread) and calls tick(); the AXObserver delivers box
changes. The wake thread only enqueues, so the state machine is single-threaded.
"""
from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from . import __version__, config as config_mod
from .commands import from_config, fixups_from_config
from .state import StateMachine, S
from .telemetry import Telemetry

_SOUNDS = {
    "armed": "Tink", "send": "Pop", "cancel": "Bottle",
    "stop": "Funk", "error": "Basso",
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
    ax = RealAX(cfg.target_bundle_id)
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
    state = {"armed_once": False}

    def tick(_timer):
        while True:
            try:
                score = q.get_nowait()
            except queue.Empty:
                break
            sm.on_wake(score)
        sm.tick()
        if once:
            if sm.state is not S.IDLE:
                state["armed_once"] = True
            elif state["armed_once"]:
                CFRunLoopStop(CFRunLoopGetCurrent())

    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.12, True, tick)

    if once:
        print("chotu --once: arming now — dictate a prompt then say your command word.")
        q.put(1.0)
    else:
        from .wake import WakeListener
        listener = WakeListener(cfg, lambda score: q.put(score))
        threading.Thread(target=listener.run, name="wake", daemon=True).start()
        print(f"chotu listening (wake='{cfg.wake_phrase}', "
              f"model='{cfg.wake_model or cfg.wake_pretrained_fallback}'). Ctrl+C to quit.")

    # CFRunLoopRun() (not NSRunLoop.run()) so CFRunLoopStop cleanly ends --once.
    # The AXObserver source and NSTimer both live on this same run loop.
    try:
        CFRunLoopRun()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="chotu", description="Wake-word voice controller for Claude Code")
    p.add_argument("--config", help="path to config.toml")
    p.add_argument("--once", action="store_true", help="arm once without the wake word (M1 smoke test)")
    p.add_argument("--read", action="store_true", help="print the Claude box AXValue once and exit (debug)")
    p.add_argument("--mic-check", action="store_true", help="record 2s and report level (verify Mic permission)")
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

    cfg = config_mod.load(_find_config(args.config))

    if args.read:
        from .ax import RealAX
        ax = RealAX(cfg.target_bundle_id)
        ax.set_manual_a11y()
        print(repr(ax.read_box()))
        return 0

    return run(cfg, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
