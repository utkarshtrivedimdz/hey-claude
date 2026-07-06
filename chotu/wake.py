"""Wake-word listener (openWakeWord). Runs on its own thread; heavy imports are
deferred so this module imports without openwakeword/sounddevice present.

Custom "chotu" needs a trained model (see scripts/train_chotu.md). Until then it
falls back to a pretrained phrase (wake.pretrained_fallback) so the daemon runs
end-to-end. on_wake is called from THIS thread — the runtime enqueues onto the
main run-loop for thread safety (see __main__).

Logs via the shared `chotu` logger (chotu/log.py): INFO for model/stream lifecycle,
DEBUG for the per-~2s heartbeat + each wake candidate, CRITICAL if the thread dies
(the daemon goes deaf). Turn on DEBUG with --debug or CHOTU_DEBUG=1.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

log = logging.getLogger(__name__)


class WakeListener:
    def __init__(self, cfg, on_wake: Callable[[float], None]):
        self.cfg = cfg
        self.on_wake = on_wake
        self._stop = False
        self._model = None
        self._key = None

    def _load(self) -> None:
        import openwakeword
        from openwakeword.model import Model
        try:
            openwakeword.utils.download_models()
        except Exception:
            pass
        fw = self.cfg.wake_inference_framework
        if self.cfg.wake_model:
            self._model = Model(wakeword_models=[self.cfg.wake_model], inference_framework=fw)
            self._key = next(iter(self._model.models.keys()))
        else:
            self._model = Model(wakeword_models=[self.cfg.wake_pretrained_fallback], inference_framework=fw)
            self._key = self.cfg.wake_pretrained_fallback

    def run(self) -> None:
        import queue as _queue
        import numpy as np
        import sounddevice as sd

        try:
            self._load()
            log.info("wake model loaded: key=%s framework=%s", self._key, self.cfg.wake_inference_framework)
            CHUNK = 1280  # 80 ms @ 16 kHz — openWakeWord's frame size
            # Capture in a real-time callback that only buffers → the queue absorbs
            # bursty Bluetooth delivery so predict() (56x realtime) never causes overflow.
            q: "_queue.Queue[bytes]" = _queue.Queue()
            ovf = [0]

            def _cb(indata, frames, time_info, status):
                if status and status.input_overflow:
                    ovf[0] += 1
                q.put(bytes(indata))

            hb, mx, cnt = time.time(), 0.0, 0
            with sd.InputStream(
                samplerate=16000, channels=1, dtype="int16",
                blocksize=CHUNK, device=self.cfg.mic_device, callback=_cb,
            ):
                log.info("mic stream open (callback): device=%s threshold=%.2f",
                         self.cfg.mic_device, self.cfg.wake_threshold)
                while not self._stop:
                    try:
                        data = q.get(timeout=0.5)
                    except _queue.Empty:
                        continue
                    audio = np.frombuffer(data, dtype="int16")
                    score = float(self._model.predict(audio).get(self._key, 0.0))
                    mx, cnt = max(mx, score), cnt + 1
                    if ovf[0]:
                        log.warning("mic input overflow ×%d — Bluetooth burst / CPU stall", ovf[0])
                        ovf[0] = 0
                    if time.time() - hb > 2.0:
                        log.debug("heartbeat chunks=%d max_score=%.3f qsize=%d rms=%d",
                                  cnt, mx, q.qsize(), int(np.abs(audio).mean()))
                        hb, mx, cnt = time.time(), 0.0, 0
                    if score >= self.cfg.wake_log_floor:
                        log.debug("wake candidate score=%.3f (floor=%.2f, thr=%.2f) → enqueue",
                                  score, self.cfg.wake_log_floor, self.cfg.wake_threshold)
                        self.on_wake(score)
        except Exception:
            log.critical("wake thread crashed — daemon is now DEAF to the wake word", exc_info=True)

    def stop(self) -> None:
        self._stop = True
