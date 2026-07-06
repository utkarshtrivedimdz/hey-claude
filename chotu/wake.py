"""Wake-word listener (openWakeWord). Runs on its own thread; heavy imports are
deferred so this module imports without openwakeword/sounddevice present.

Custom "chotu" needs a trained model (see scripts/train_chotu.md). Until then it
falls back to a pretrained phrase (wake.pretrained_fallback) so the daemon runs
end-to-end. on_wake is called from THIS thread — the runtime enqueues onto the
main run-loop for thread safety (see __main__).
"""
from __future__ import annotations

from typing import Callable


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
        if self.cfg.wake_model:
            self._model = Model(wakeword_models=[self.cfg.wake_model])
            self._key = next(iter(self._model.models.keys()))
        else:
            self._model = Model(wakeword_models=[self.cfg.wake_pretrained_fallback])
            self._key = self.cfg.wake_pretrained_fallback

    def run(self) -> None:
        import numpy as np
        import sounddevice as sd

        self._load()
        CHUNK = 1280  # 80 ms @ 16 kHz — openWakeWord's frame size
        with sd.InputStream(
            samplerate=16000, channels=1, dtype="int16",
            blocksize=CHUNK, device=self.cfg.mic_device,
        ) as stream:
            while not self._stop:
                data, _ = stream.read(CHUNK)
                audio = np.frombuffer(bytes(data), dtype="int16")
                preds = self._model.predict(audio)
                score = float(preds.get(self._key, 0.0))
                if score >= self.cfg.wake_log_floor:
                    self.on_wake(score)

    def stop(self) -> None:
        self._stop = True
