"""Live wake-word tester — verify detection in isolation (no state machine, no keystrokes).

Runs ONLY the openWakeWord model against the mic and prints a live bar of the score,
flagging every crossing of the configured threshold. Nothing is typed anywhere; VS Code
is never touched. Use this to confirm "hey jarvis" (or a trained "hey-claude") fires reliably
and to tune wake.threshold before running the full daemon.

    .venv/bin/python -m scripts.wake_listen                 # use config.toml + defaults
    .venv/bin/python -m scripts.wake_listen --config config.toml
    .venv/bin/python -m scripts.wake_listen --floor 0.05    # print quieter near-misses

Ctrl-C to stop; prints a summary of detections.
"""
from __future__ import annotations

import argparse
import sys
import time

from hey_claude import config as _config


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Live wake-word score tester")
    p.add_argument("--config", help="path to config.toml")
    p.add_argument("--floor", type=float, default=None,
                   help="print scores >= this (default: wake.log_floor from config)")
    p.add_argument("--all", action="store_true",
                   help="print every chunk (with RMS amplitude), not just score>=floor")
    p.add_argument("--rms", type=int, default=250,
                   help="with --all, only print chunks with RMS >= this (skip silence)")
    p.add_argument("--device", type=int, default=None,
                   help="override mic device index (see sounddevice.query_devices); "
                        "bypasses config to A/B test built-in vs Bluetooth mic")
    p.add_argument("--threshold", type=float, default=None,
                   help="override wake.threshold (non-destructive; does not touch config.toml)")
    args = p.parse_args(argv)

    cfg = _config.load(args.config)
    if args.device is not None:
        cfg.mic_device = args.device
    if args.threshold is not None:
        cfg.wake_threshold = args.threshold
    floor = args.floor if args.floor is not None else cfg.wake_log_floor
    thr = cfg.wake_threshold

    import numpy as np
    import sounddevice as sd
    import openwakeword
    from openwakeword.model import Model

    try:
        openwakeword.utils.download_models()
    except Exception:
        pass

    if cfg.wake_model:
        model = Model(wakeword_models=[cfg.wake_model],
                      inference_framework=cfg.wake_inference_framework)
        key = next(iter(model.models.keys()))
    else:
        model = Model(wakeword_models=[cfg.wake_pretrained_fallback],
                      inference_framework=cfg.wake_inference_framework)
        key = cfg.wake_pretrained_fallback

    print(f"model key = {key!r}   framework = {cfg.wake_inference_framework}   "
          f"device = {cfg.mic_device}   threshold = {thr:.2f}   print floor = {floor:.2f}")
    print("Speak the wake word. Ctrl-C to stop.\n")

    import queue as _queue

    CHUNK = 1280  # 80 ms @ 16 kHz
    REFRACTORY = 0.6  # s; absorb intra-utterance score dips so one "hey jarvis" = one event
    peak = 0.0
    events = []          # [t_since_start, peak_score_of_burst]
    above = False        # currently inside a burst?
    last_event_t = -999.0

    # Non-blocking capture (mirrors hey_claude/wake.py): the callback ONLY buffers into a queue,
    # so predict()/print latency can never overflow the mic and drop audio.
    q: "_queue.Queue[bytes]" = _queue.Queue()
    ovf = [0]

    def _cb(indata, frames, time_info, status):
        if status and status.input_overflow:
            ovf[0] += 1
        q.put(bytes(indata))

    t0 = time.monotonic()
    try:
        with sd.InputStream(samplerate=16000, channels=1, dtype="int16",
                            blocksize=CHUNK, device=cfg.mic_device, callback=_cb):
            while True:
                try:
                    data = q.get(timeout=0.5)
                except _queue.Empty:
                    continue
                audio = np.frombuffer(data, dtype="int16")
                score = float(model.predict(audio).get(key, 0.0))
                rms = int(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
                now = time.monotonic() - t0
                peak = max(peak, score)
                if ovf[0]:
                    print(f"  [mic overflow ×{ovf[0]}]", file=sys.stderr)
                    ovf[0] = 0

                # rising-edge utterance counter with refractory
                if score >= thr:
                    if not above and (now - last_event_t) > REFRACTORY:
                        events.append([now, score])   # new utterance
                    above = True
                    events[-1][1] = max(events[-1][1], score)
                    last_event_t = now  # extend refractory while still hot
                elif score < floor:
                    above = False

                show = score >= floor or (args.all and rms >= args.rms)
                if show:
                    bar = "#" * int(score * 40)
                    if score >= thr:
                        tag = f"  <<< FIRED  (t={now:4.1f}s)"
                    elif rms >= args.rms:
                        tag = "  (speech, rejected)" if score < floor else ""
                    else:
                        tag = ""
                    print(f"  rms={rms:5d}  {score:0.3f} |{bar:<40}|{tag}")
    except KeyboardInterrupt:
        print("\n\n=== DETECTION COUNT ===")
        for i, (t, s) in enumerate(events, 1):
            print(f"  #{i}  t={t:5.1f}s   peak={s:.3f}")
        print(f"\n  total 'hey jarvis' detections = {len(events)}   overall peak = {peak:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
