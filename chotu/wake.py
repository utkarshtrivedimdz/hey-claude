"""Wake-word listener (openWakeWord). Runs on its own thread; heavy imports are
deferred so this module imports without openwakeword/sounddevice present.

Custom "chotu" needs a trained model (see scripts/train_chotu.md). Until then it
falls back to a pretrained phrase (wake.pretrained_fallback) so the daemon runs
end-to-end. on_wake is called from THIS thread — the runtime enqueues onto the
main run-loop for thread safety (see __main__).

Set CHOTU_WAKE_DEBUG=1 to log a per-~2s heartbeat (chunk count + max score) and
any wake-thread crash to ~/Library/Logs/hey-claude/wake-debug.log.
"""
from __future__ import annotations

import os
import time
import traceback
from pathlib import Path
from typing import Callable

_DEBUG = os.environ.get("CHOTU_WAKE_DEBUG") == "1"
_DBG_PATH = Path("~/Library/Logs/hey-claude/wake-debug.log").expanduser()


def _dbg(msg: str) -> None:
    try:
        _DBG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _DBG_PATH.open("a") as f:
            f.write(f"{time.time():.1f} {msg}\n")
    except OSError:
        pass


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
        import numpy as np
        import sounddevice as sd

        try:
            self._load()
            _dbg(f"model loaded: key={self._key}")
            CHUNK = 1280  # 80 ms @ 16 kHz — openWakeWord's frame size
            hb, mx, cnt = time.time(), 0.0, 0
            with sd.InputStream(
                samplerate=16000, channels=1, dtype="int16",
                blocksize=CHUNK, device=self.cfg.mic_device,
            ) as stream:
                _dbg(f"stream open: device={self.cfg.mic_device} thr={self.cfg.wake_threshold}")
                while not self._stop:
                    data, overflow = stream.read(CHUNK)
                    audio = np.frombuffer(bytes(data), dtype="int16")
                    score = float(self._model.predict(audio).get(self._key, 0.0))
                    mx, cnt = max(mx, score), cnt + 1
                    if _DEBUG and time.time() - hb > 2.0:
                        _dbg(f"heartbeat chunks={cnt} max_score={mx:.3f} overflow={overflow} rms={int(np.abs(audio).mean())}")
                        hb, mx, cnt = time.time(), 0.0, 0
                    if score >= self.cfg.wake_log_floor:
                        self.on_wake(score)
        except Exception:
            _dbg("WAKE THREAD CRASHED:\n" + traceback.format_exc())

    def stop(self) -> None:
        self._stop = True
