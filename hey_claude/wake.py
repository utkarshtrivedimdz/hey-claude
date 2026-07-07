"""Wake-word listener (openWakeWord). Runs on its own thread; heavy imports are
deferred so this module imports without openwakeword/sounddevice present.

Custom "hey-claude" needs a trained model (see scripts/train-wake-word.md). Until then it
falls back to a pretrained phrase (wake.pretrained_fallback) so the daemon runs
end-to-end. on_wake is called from THIS thread — the runtime enqueues onto the
main run-loop for thread safety (see __main__).

Logs via the shared `hey-claude` logger (hey_claude/log.py): INFO for model/stream lifecycle,
DEBUG for the per-~2s heartbeat + each wake candidate, CRITICAL if the thread dies
(the daemon goes deaf). Turn on DEBUG with --debug or HEY_CLAUDE_DEBUG=1.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

log = logging.getLogger(__name__)


def _reinit_portaudio(sd) -> None:
    """Force PortAudio to re-enumerate audio devices.

    A long-lived process initializes PortAudio once (at sounddevice import) and caches the
    device list. After the input device changes — a Bluetooth mic dropping and returning —
    that cache goes stale and every InputStream open returns -9986 FOREVER in this process,
    even though a fresh process opens the same device fine. terminate()+initialize() resets
    the CoreAudio HAL; it's the sanctioned sounddevice workaround for device-topology
    changes. Best-effort: log and continue if it raises (we then open with cached state).
    """
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        log.warning("PortAudio re-init failed; opening with cached device state", exc_info=True)


class WakeListener:
    def __init__(self, cfg, on_wake: Callable[[float], None],
                 on_mic_lost: Callable[[], None] | None = None):
        self.cfg = cfg
        self.on_wake = on_wake
        # Called (from this thread) when the mic stops delivering audio — i.e. the input
        # device disconnected (Bluetooth headset dropped). The runtime turns this into a
        # clean daemon stop so it doesn't sit silently deaf; recovery is manual (reconnect
        # the mic + restart). None => no-op (e.g. tests).
        self.on_mic_lost = on_mic_lost
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

        # Re-enumerate devices before (re)opening: a wake thread that restarts after a
        # device change (Bluetooth drop/return) inherits this process's stale PortAudio
        # cache and would otherwise fail every open with -9986. See _reinit_portaudio.
        _reinit_portaudio(sd)

        try:
            self._load()
            log.info("wake model loaded: key=%s framework=%s", self._key, self.cfg.wake_inference_framework)
            CHUNK = 1280  # 80 ms @ 16 kHz — openWakeWord's frame size
            # No-audio watchdog: PortAudio calls _cb continuously (~every 80 ms) even in
            # silence, so a prolonged gap means the device stopped delivering — i.e. it
            # disconnected (Bluetooth headset dropped). A `device=None` stream binds to a
            # concrete device at open time and does NOT follow a default-device change, so
            # the callback simply ceases. 5 s is well past normal Bluetooth burst jitter.
            STALL_S = 5.0
            # Capture in a real-time callback that only buffers → the queue absorbs
            # bursty Bluetooth delivery so predict() (56x realtime) never causes overflow.
            q: "_queue.Queue[bytes]" = _queue.Queue()
            ovf = [0]
            last_cb = [time.time()]  # wall-clock of the most recent callback (stall watchdog)

            def _cb(indata, frames, time_info, status):
                last_cb[0] = time.time()
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
                    gap = time.time() - last_cb[0]
                    if gap > STALL_S:
                        log.critical("mic input lost — no audio for %.0fs (device likely "
                                     "disconnected); wake thread exiting, daemon stays up "
                                     "and marks not-listening (click menu bar to retry)", gap)
                        if self.on_mic_lost:
                            self.on_mic_lost()
                        return
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
