# Training the "hey claude" wake word (openWakeWord)

"hey claude" isn't in openWakeWord's pretrained set, so we generate a custom model from
**synthetic speech** — no recording sessions needed. This runs once; the daemon uses
a pretrained fallback phrase until you point `wake.model` at the result.

## Option 1 — openWakeWord's automatic training (recommended)

openWakeWord ships a synthetic-data training pipeline (Piper TTS → augment with
noise/reverb → train a small classifier). See the project's
`automatic_model_training` notebook/colab.

Rough flow:

1. Install training extras (heavier than the runtime deps):
   ```bash
   pip install openwakeword[training] piper-tts
   ```
2. Generate positive clips for the phrase **"hey claude"** (Piper TTS, many voices/speeds)
   and mix in negatives + background noise (the pipeline handles augmentation).
3. Train → export `hey_claude.onnx` (or `.tflite`).
4. Point config at it:
   ```toml
   [wake]
   model = "/absolute/path/to/hey_claude.onnx"
   threshold = 0.5
   ```
5. Tune `threshold` from real use — `python scripts/stats.py` reports the false-trigger
   rate and a wake-score split so you can pick the knee. Start ~0.5.

## Option 2 — Porcupine (fallback engine)

If openWakeWord's "hey claude" proves too false-fire-prone, Porcupine builds a custom
keyword from its web console (needs a free Picovoice key). Would require swapping
`wake.py` for a Porcupine listener — kept as a documented fallback (REQUIREMENTS Q1).

## Notes

- "hey claude" is 2 syllables with distinct phonemes → trains cleanly and is rare in
  normal English speech (low false-trigger surface).
- Keep the model in `models/` (gitignored). Only `wake.model` in config.toml points
  to it.
- The wake word only *arms* hey-claude; all commands are read from the box (no per-command
  models to train).
