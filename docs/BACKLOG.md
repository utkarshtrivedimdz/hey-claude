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

- [ ] **Run desktop-wide (any app, not just the geofast VS Code workspace).**
  Requested 2026-07-06 — chotu should be a long-running, always-available voice
  controller for the whole desktop, not bound to one window. Today the focus-safety
  gate hard-binds it to a single VS Code workspace (`target.bundle_id` / `workspace`
  / `title_substr`); the goal is to arm on wake and dictate+act into whatever window
  is focused (or a configurable allow-list of apps). Needs: (a) generalize the focus
  gate from one workspace to a `targets` allow-list (never type into an unlisted
  window — keep it fail-closed); (b) per-app keymaps — dictation start + send/cancel
  differ per app (`Cmd+D` is Claude/VS Code-specific); (c) generalized box read/write
  (AXValue is broad, but not every app exposes an editable `AXTextArea`). Big scope
  item — likely its own design doc + ADR before it spreads.

- [ ] **Press-by-name via AXPress.** Verified 2026-07-06: when a tab is active,
  Claude Code's question dialogs expose their options as `AXRadioButton` and the
  `Submit answers` / `Close` as `AXButton`, all with the `AXPress` action — and
  panel controls (`Voice dictation`, `New session`, `Add`, `Bypass permissions`)
  are `AXPress`-able too. Add commands that (a) find an element whose label contains
  a spoken keyword and `AXPress` it, (b) `AXPress` a named button. Enables answering
  choice/permission dialogs and hitting panel buttons entirely by voice.
  - **Constraint:** only the ACTIVE tab is in the AX tree — a dialog in a background
    tab is invisible; the command must focus that tab first (or fail with a cue).
  - **Bonus:** `AXPress` the `Voice dictation` button instead of `Cmd+D` — avoids the
    VS Code multi-cursor collision; more robust dictation start.
- [ ] Keystroke fallback for widgets that don't expose `AXPress` (↑/↓ + Enter, Esc).

## UX

- [ ] **Menu-bar state indicator** (idle / armed / dictating). It's headless now, so
  there's no visual cue that it armed. Biggest quality-of-life upgrade. (§10)
- [ ] **Spoken readback / confirm** before send, for fully eyes-free use. (§10)

## Robustness / ops

- [ ] **LaunchAgent as a signed `.app` bundle** for clean TCC identity — the
  raw-`python`-binary Mic/Accessibility grant for launchd is finicky. Only if the
  plain LaunchAgent grant misbehaves.
- [ ] **Move repo + venv off the external volume** (`/Volumes/GeofastStorage`) —
  now *mitigated* (not fixed) by the internal launcher, which waits up to 5 min for
  the volume to mount before starting chotu. Moving to internal disk would remove the
  wait + the "drive unplugged = never runs" failure mode entirely. Lower priority now.
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

- [x] **Auto-start at login via LaunchAgent + internal launcher.** Shipped 2026-07-06.
  `setup.sh` installs a launcher on the internal disk (`~/Library/Application
  Support/hey-claude/launch-chotu.sh`) and a `RunAtLoad`/`KeepAlive` LaunchAgent that
  runs it; the launcher waits for the repo's venv before starting chotu, so login
  survives the boot mount-race even with the repo on an external USB volume. Verified:
  mic stream opens + `focus_gate=pass` under launchd (Accessibility + Microphone carry
  over). Templates: `launch-chotu.sh.template`, `com.hey-claude.chotu.plist.template`.
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
