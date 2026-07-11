# hey-claude — Backlog

Actionable task list (the aspirational roadmap lives in
[`REQUIREMENTS.md` §10](REQUIREMENTS.md)). Roughly prioritized; requested items up top.

## Near-term

- [ ] **Train the "hey-claude" wake model (highest-leverage for accuracy).** Replace the
  `hey_jarvis` fallback. Do it with **positives recorded through the actual Bluetooth
  mic** (domain match) — scores swung 0.4–0.95 on the generic model; a personalized
  one should be far tighter. See `scripts/train-wake-word.md`; set `wake.model` after.

- [ ] **Tune `wake.threshold` from telemetry.** Run `scripts/stats.py`, read the
  wake-score distribution, set the threshold at the knee (~0.4 for this mic). Same
  loop for false-trigger rate and the Q11b command prefix.

- [ ] **Run desktop-wide (any app, not just the geofast VS Code workspace).**
  Requested 2026-07-06 — hey-claude should be a long-running, always-available voice
  controller for the whole desktop, not bound to one window. Today the focus-safety
  gate hard-binds it to a single VS Code workspace (`target.bundle_id` / `workspace`
  / `title_substr`); the goal is to arm on wake and dictate+act into whatever window
  is focused (or a configurable allow-list of apps). Needs: (a) generalize the focus
  gate from one workspace to a `targets` allow-list (never type into an unlisted
  window — keep it fail-closed); (b) per-app keymaps — dictation start + send/cancel
  differ per app (`Cmd+D` is Claude/VS Code-specific); (c) generalized box read/write
  (AXValue is broad, but not every app exposes an editable `AXTextArea`). Big scope
  item — likely its own design doc + ADR before it spreads.

- [x] **Silence timeout replaced by button-driven off-ramp.** Done 2026-07-06 (see
  DICTATION-AX-PLAN.md). The fixed `disarm_s` silence timer is gone: the Voice-dictation
  button's AXTitleChanged event is the ground truth, so a turn ends on a command word or a
  button-off event (OS/user turning the mic off), never a pause timer. A long think-pause
  no longer disarms. (A VAD off-ramp is no longer needed for this; the `arm_s` ceiling
  still backstops a false wake that never turns the button on.)

- [x] **Press-by-name via AXPress.** Done 2026-07-07 (see PRESS-BY-NAME-PLAN.md).
  `"<prefix> press <label>"` (e.g. "okay press submit", "okay press yes") AXPresses the
  first `AXButton`/`AXRadioButton` whose label *contains* the spoken keyword — answers
  Claude Code choice/permission dialogs and hits panel buttons by voice. Reuses the
  dictate→observe-box loop: the phrase is dictated in, matched by `Commands.match_press`,
  and instead of sending, `RealAX.press_by_name` walks the AX tree (`_bfs_find`, extracted
  from `_find_element`) and presses; the dictated command text is cleared, never submitted.
  Config: `[commands] press_verbs` / `press_roles`.
  - **Constraint (still open):** only the ACTIVE tab is in the AX tree — a dialog in a
    background tab is invisible; we fail with the error beep + a log line, we do NOT yet
    focus the tab first. Follow-up below.
- [ ] **Press-by-name: focus the target tab first** when the control isn't in the active
  tab's AX tree (today: fail with a cue). Needs tab enumeration + activation.
- [ ] Keystroke fallback for widgets that don't expose `AXPress` (↑/↓ + Enter, Esc).

## UX

- [~] **Menu-bar state indicator** (idle / armed / dictating) — **descoped 2026-07-06.**
  The VS Code Voice-dictation button already IS the indicator: hey-claude drives + observes it
  (AXPress + AXTitleChanged is the DICTATING ground truth), so blue = listening, off = not.
  That's the same signal a menu-bar dot would show, and the user confirms it's sufficient.
  Only revisit if we go **desktop-wide** (no VS Code button visible in other apps). (§10)
- [ ] **Spoken readback / confirm** before send, for fully eyes-free use. (§10)

- [ ] **Attention sounds — "Claude needs you" audio cues.** Requested 2026-07-11. One
  theme, two triggers (share the beep, differ in detection — build as one item, ship
  the two triggers independently):
  - **(a) Run-complete / waiting-for-input.** When Claude Code finishes its turn and
    goes idle waiting on the user, play a distinct sound so the user knows it's their
    move without watching the screen. Detection: the Voice-dictation button + AX panel
    state we already observe should expose the idle/awaiting transition (reuse the
    `AXTitleChanged`/AX-tree signal that drives the DICTATING ground truth); pick the
    "stopped generating → prompt is idle" edge, debounce so it fires once per turn.
  - **(b) Dialog-box appeared.** Recognize when a choice/permission/confirm dialog pops
    up (the same `AXButton`/`AXRadioButton` panels `press_by_name` already walks) and
    play a *different* sound, cueing the user that a response is required. Detection is
    largely built: the AX tree walk exists; add an observer edge for "dialog rendered"
    and beep on appearance.
  - Distinct sounds per trigger (like the `deaf` beep pattern); make them
    configurable/mutable in `[sounds]` (or `[commands]`) and off-able. Cheap MVP once
    the AX edges are identified; the risk is reliable edge detection, not playback.

## Robustness / ops

- [x] **Recover from mic device loss (Bluetooth drop silently deafened hey-claude).**
  Done 2026-07-07. Symptom (2026-07-06): disconnecting the OnePlus Buds mid-session
  left the daemon alive but deaf — the wake stream is opened once at startup bound to
  `device=None` (the default input *at that time*); when that device disappears
  PortAudio stops calling the callback, with no error logged, so hey-claude looked running
  but heard nothing until a manual restart. **Chosen approach: detect-and-stop with
  manual recovery** (user preference — they restart when they reconnect the headset),
  not self-heal/re-open. `wake.py` runs a no-audio watchdog: no callback for 5 s ⇒
  `on_mic_lost` ⇒ `__main__` logs CRITICAL, plays the `deaf` beep (Submarine), and
  cleanly stops the run loop (exit 0). The LaunchAgent `KeepAlive` is now
  `{SuccessfulExit: false}` so a clean stop stays down (manual reconnect + restart)
  while a real crash still respawns. Flow: disconnect → beep + stop; reconnect →
  `launchctl kickstart`/reload to resume (mic re-binds to the live device on startup).

- [x] **In-process recovery from a wedged audio HAL (deaf without a process restart).**
  Done 2026-07-07. Follow-up to the item above: a second incident (2026-07-07) showed a
  *different* failure than the stall watchdog covers. PortAudio raised at **stream-open**
  (`PaErrorCode -9986`) when the Buds dropped mid-reconnect — caught by `wake.py`'s generic
  `except` (thread exits, logs CRITICAL), so the process stayed **alive but deaf** and
  `on_mic_lost` never fired (launchd's `SuccessfulExit:false` never got its clean exit).
  Worse: because sounddevice initializes PortAudio **once at import** and caches the device
  list, that process could **never** reopen the input again — every retry (incl. manual
  menu-bar toggles) returned `-9986`, while a *fresh* process opened the same device
  instantly. **Fix:** `wake.py:_reinit_portaudio` runs `sd._terminate()`+`sd._initialize()`
  at the start of every wake-thread `run()`, re-enumerating devices so a (re)start sees
  current topology; and `__main__._toggle` now self-heals — a click while listening-but-deaf
  restarts the listener (one click, not two) instead of muting a dead thread. This *reverses*
  the earlier "deferred auto self-heal" note for the crash-at-open case. Verify: crash the
  wake thread via a Bluetooth drop, reconnect, click once → `mic stream open` succeeds in
  the same process (no restart).

- [x] **Stall watchdog stays up instead of exiting (unify with the click-to-heal path).**
  Done 2026-07-07. The stall watchdog (first item above) still cleanly exited on a mic drop
  while the crash-at-open path (second item) stayed up and self-healed — two behaviors for
  the same user-visible event (Buds dropped). Now the watchdog matches: on `mic_lost`,
  `__main__.tick` keeps the process alive, plays the `deaf` beep once, and repaints the menu
  bar icon to not-listening (`set_listening(False)`) instead of stopping the run loop; a
  click self-heals via the existing `_toggle` branch (`state["listening"]` stays `True`, so
  the click-while-deaf case fires → restart listener + re-init PortAudio). The clean-exit
  path is now only the fallback when the menu bar is **disabled** (no icon to click), where
  `KeepAlive={SuccessfulExit:false}` still leaves it down for a manual restart. Verified
  end-to-end 2026-07-07: drop → icon flips + daemon stays up → reconnect + one click →
  `mic stream open` in the same process, twice in a row. This closes the "deferred auto
  self-heal" note on the first item for the stall path too.

- [ ] **LaunchAgent as a signed `.app` bundle** for clean TCC identity — the
  raw-`python`-binary Mic/Accessibility grant for launchd is finicky. Only if the
  plain LaunchAgent grant misbehaves.
- [ ] **Move repo + venv off the external volume** (`/Volumes/GeofastStorage`) —
  now *mitigated* (not fixed) by the internal launcher, which waits up to 5 min for
  the volume to mount before starting hey-claude. Moving to internal disk would remove the
  wait + the "drive unplugged = never runs" failure mode entirely. Lower priority now.
- [ ] **Re-register the AXObserver on focus** each turn (a sent message re-renders the
  panel; within-a-turn is stable — verified). Harden for multi-turn sessions. (Q12)
- [ ] **Log wake-window audio (redacted) for retraining** — feed real voice+mic
  samples back into model training so it keeps improving. Respect FR-7 redaction.

## Later (§10 roadmap)

- [ ] **Voice snippets / macros** — keyword → hey-claude types a canned block (proven
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
  Support/hey-claude/launch-hey-claude.sh`) and a `RunAtLoad`/`KeepAlive` LaunchAgent that
  runs it; the launcher waits for the repo's venv before starting hey-claude, so login
  survives the boot mount-race even with the repo on an external USB volume. Verified:
  mic stream opens + `focus_gate=pass` under launchd (Accessibility + Microphone carry
  over). Templates: `launch-hey-claude.sh.template`, `com.hey-claude.plist.template`.
- [x] **Dictation fixups (substitution map).** Requested + shipped 2026-07-06 —
  `[fixups]` table in `config.toml` maps mishearings → corrections (case-insensitive,
  whole-word, longest key wins). Applied to the prompt after command strip, before
  Return; a real change triggers the read→rewrite path (`Cmd+A` + retype), otherwise
  the fast backspace+Return send is untouched. `Fixups` in `commands.py`, applied in
  `actions.py` `Actions.perform`; seeded with `clot`/`clod`(`code`) → `Claude`(` Code`).
- [x] Core loop: wake → bootstrap → dictate → read box → strip command → send.
- [x] Token-based command matching (handles dictation punctuation "Okay. Send.").
- [x] onnxruntime pin (<1.19) — fixed silent 0.000 wake scores.
- [x] Callback+queue audio capture — fixed Bluetooth overflow.
- [x] FR-7 telemetry + `stats.py`; `--mic-check`; LaunchAgent template.
