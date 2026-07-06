# hey-claude — Backlog

Actionable task list (the aspirational roadmap lives in
[`REQUIREMENTS.md` §10](REQUIREMENTS.md)). Roughly prioritized; requested items up top.

## Near-term

- [ ] **Train the "chotu" wake model (highest-leverage for accuracy).** Replace the
  `hey_jarvis` fallback. Do it with **positives recorded through the actual Bluetooth
  mic** (domain match) — scores swung 0.4–0.95 on the generic model; a personalized
  one should be far tighter. See `scripts/train_chotu.md`; set `wake.model` after.

- [ ] **Tune `wake.threshold` from telemetry.** Run `scripts/stats.py`, read the
  wake-score distribution, set the threshold at the knee (~0.4 for this mic). Same
  loop for false-trigger rate and the Q11b command prefix.

## UX

- [ ] **Menu-bar state indicator** (idle / armed / dictating). It's headless now, so
  there's no visual cue that it armed. Biggest quality-of-life upgrade. (§10)
- [ ] **Spoken readback / confirm** before send, for fully eyes-free use. (§10)

## Robustness / ops

- [ ] **LaunchAgent as a signed `.app` bundle** for clean TCC identity — the
  raw-`python`-binary Mic/Accessibility grant for launchd is finicky. Only if the
  plain LaunchAgent grant misbehaves.
- [ ] **Move repo + venv off the external volume** (`/Volumes/GeofastStorage`) or
  document the dependency — the LaunchAgent can't start if the volume isn't mounted
  at login.
- [ ] **Re-register the AXObserver on focus** each turn (a sent message re-renders the
  panel; within-a-turn is stable — verified). Harden for multi-turn sessions. (Q12)
- [ ] **Log wake-window audio (redacted) for retraining** — feed real voice+mic
  samples back into model training so it keeps improving. Respect FR-7 redaction.

## Later (§10 roadmap)

- [ ] **Voice snippets / macros** — keyword → chotu types a canned block (proven
  feasible via keystroke write, Q11a).
- [ ] **Natural-language commands** — match natural phrasing from the box, beyond
  fixed words (no new models; Q2/Q11b).
- [ ] **Terminal Claude Code support** — its own keymap + focus/detection (out of
  scope for v1, §7).
- [ ] **Fallback B for commands** — separate trained `send`/`cancel` models if Option
  A's strip/disambiguation misfires too often (Q11b).

## Done (shipped in v1)

- [x] **Dictation fixups (substitution map).** Requested + shipped 2026-07-06 —
  `[fixups]` table in `config.toml` maps mishearings → corrections (case-insensitive,
  whole-word, longest key wins). Applied to the prompt after command strip, before
  Return; a real change triggers the read→rewrite path (`Cmd+A` + retype), otherwise
  the fast backspace+Return send is untouched. `Fixups` in `commands.py`, wired via
  `state.py` `_dispatch`; seeded with `clot`/`clod`(`code`) → `Claude`(` Code`).
- [x] Core loop: wake → bootstrap → dictate → read box → strip command → send.
- [x] Token-based command matching (handles dictation punctuation "Okay. Send.").
- [x] onnxruntime pin (<1.19) — fixed silent 0.000 wake scores.
- [x] Callback+queue audio capture — fixed Bluetooth overflow.
- [x] FR-7 telemetry + `stats.py`; `--mic-check`; LaunchAgent template.
