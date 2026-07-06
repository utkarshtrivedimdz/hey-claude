# hey-claude — Hardening Plan

Addresses the architecture critique (2026-07-06): doc↔code drift, missing transition
chokepoint, dispatch buried in the state machine, fragile box observation, the Option-A
premature-send race, unverified dictation start, and silent degradation on extension
churn. Ordered by leverage and dependency; each phase is independently shippable and
leaves the tree green.

**Cross-cutting rule:** every phase's Definition of Done includes updating the affected
[`ARCHITECTURE.md`](ARCHITECTURE.md) diagram in the SAME change. Docs move with code —
no more drift. Diagrams that describe not-yet-built behavior carry a one-line
`Status: planned (HARDENING-PLAN Phase N)` marker until the phase lands.

---

## Guiding constraints

- Keep the hexagonal split intact: pure logic (`state`, `commands`, new `actions`) stays
  I/O-free and fake-tested; adapters (`ax`, `keys`, `system`) stay thin.
- No new runtime threads. Everything stays on the CFRunLoop; `tick()` (0.12 s) is the
  only polling budget.
- Every behavior change ships with a fake-clock/fake-port test before it's called done.
- Brittle external contracts (AX descriptions, DOM classes, keycodes) get a **loud**
  failure path, never silent degradation.

---

## Phase 0 — Reconcile the docs with reality (docs only, no code)

**Gap:** §2/§3/§4 diagrams describe methods and an `acting` state that don't exist.
For a repo that claims "every mechanism verified," the UML is the least verified artifact.

**Changes** ([ARCHITECTURE.md](ARCHITECTURE.md)):
- Fix outright-wrong names that are NOT in this plan's scope, to match current code:
  `WakeListener.start/_score_loop` → `run`; `AX.observe` → `start_observing`/`stop_observing`.
- Reframe the diagrams as **target state** for this plan (they already show
  `read_button_state`, `tick() liveness`, the button off-ramp). Add the
  `Status: planned (Phase N)` marker to each forward-looking element so the doc is honest
  about what's built vs designed.
- The `acting` state: keep it in the diagram as the target (Phase 2 makes it real by
  extracting dispatch), marked planned. Today's inline dispatch is the thing Phase 2 removes.

**DoD:** a reader can map every diagram box/edge to either current code or a named phase.
**Risk:** none (docs). **Depends on:** nothing. **Est:** small.

---

## Phase 1 — Single transition chokepoint + transition log

**Gap:** `self.state = …` is assigned at ~10 sites; illegal transitions are silently
possible and nothing logs state changes — despite telemetry-driven tuning being the
whole point.

**Changes** ([state.py](../chotu/state.py)):
- Add `_transition(self, new: S, reason: str)`: assert `(self.state → new)` is in a
  declared `LEGAL: dict[S, set[S]]` table; on illegal, log an error event and (test builds)
  raise; centralize the state-change beep here.
- Replace every `self.state = …` with `self._transition(...)`.
- Emit a lightweight `state_transition` telemetry record (from, to, reason, monotonic)
  via a new `Telemetry.log_transition`. Keep it cheap; it's the trace we currently lack.

**Tests** ([tests/test_state.py](../tests/test_state.py)): assert the legal-transition
table is exhaustive; assert an illegal transition raises; assert every existing scenario
still ends in the same terminal state and now emits the expected transition sequence.

**DoD:** no raw `self.state =` outside `_transition`; transitions appear in the event log.
**Risk:** low (pure logic). **Depends on:** none. **Est:** small.

---

## Phase 2 — Extract the dispatch/actions layer (make `acting` real)

**Gap:** `_dispatch` (~45 lines, [state.py:107-150](../chotu/state.py#L107-L150)) mixes
command semantics, fixups, keystroke choreography, telemetry, and the correction window
inside the "pure" state machine.

**Changes:**
- New `chotu/actions.py`: `Actions.perform(match, box_text) -> ActionOutcome`, holding the
  send / cancel / stop keystroke choreography + fixup rewrite-vs-backspace decision. Takes
  injected `keys`/`ax` ports — still fake-testable, but out of the state machine.
- `state.py` gains a real `ACTING` transition: `on_box_change` match → `_transition(ACTING)`
  → `actions.perform(...)` → telemetry → `_transition(IDLE)`. State machine keeps *sequencing*
  and telemetry; `Actions` owns *how* each command is executed.
- Correction-window bookkeeping (`_last_send`) stays in the state machine (it's cross-turn
  state), fed by the `ActionOutcome`.

**Tests:** new `tests/test_actions.py` (send/cancel/stop, fixup rewrite path, empty-box
no-op) with fake keys/ax; `test_state.py` shrinks to sequencing assertions.

**DoD:** `_dispatch` is gone from `state.py`; `ACTING` is a real state; §4 diagram marker flips
to verified. **Risk:** medium (touches the hottest path). **Depends on:** Phase 1. **Est:** medium.

---

## Phase 3 — Box-observation robustness (stale AX refs)

**Gap:** the AXObserver binds one focused-element ref captured at dictation start
([ax.py:70-88](../chotu/ax.py#L70-L88)); ax.py's own header says webview re-renders make
refs stale. A mid-turn re-render silently stops `AXValueChanged`; only the silence timeout
catches it. The advertised `reregister_on_focus()` doesn't exist.

**Changes** ([ax.py](../chotu/ax.py)):
- Implement `reregister_on_focus()`: on a focus-change notification (or on read failure),
  re-resolve `AXFocusedUIElement` and rebind the observer to the new ref.
- Add a `tick()`-driven reconciliation in `state.py`: if the observer has delivered nothing
  for K ticks while `button == recording`, do a direct `read_box()` and, if the ref is stale,
  re-register. (The button-live signal from Phase 5 tells us "should have activity" — until
  then, gate on wall-clock.)

**Tests:** fake AX that goes stale mid-turn → assert re-registration fires and box updates
resume; assert no re-register storm when refs are healthy.

**DoD:** a simulated mid-turn re-render no longer drops the turn to a timeout.
**Risk:** medium (real AX behavior is hard to fake perfectly — pair with a manual
`--once` re-render check). **Depends on:** Phase 1. **Est:** medium.

---

## Phase 4 — Premature-send settle window

**Gap:** `match` fires on the first `on_box_change` whose trailing tokens satisfy a command
([state.py:86-88](../chotu/state.py#L86-L88)). A partial transcription reading "…okay send"
that would become "…okay send me the log" dispatches early.

**Changes** ([state.py](../chotu/state.py)):
- On a match, don't dispatch immediately: record `pending_match` + timestamp, and only
  dispatch on the next `tick()` if the box text is unchanged since the match (stable for one
  ~120 ms tick, tunable `settle_ms`). Any further `on_box_change` that changes the trailing
  tokens supersedes the pending match.
- Guard: `settle_ms` is a config timeout under `[timeouts]`.

**Tests:** partial "…okay send" followed by "…okay send me the log" → no early send; a stable
"…okay send" → sends after the settle tick.

**DoD:** the growing-partial case no longer mis-sends. **Risk:** low. **Depends on:** Phase 1
(transitions), ideally after Phase 2. **Est:** small.

---

## Phase 5 — Button-state signal (liveness + start-confirmation + off-ramp)

**Gaps:** unverified dictation start (Cmd+D may no-op on the multi-cursor collision), the
fixed-timer liveness problem, and a second non-box health signal. This is the feature that
motivated the whole thread; it attacks three gaps at once.

**Changes:**
- [ax.py](../chotu/ax.py): `read_button_state() -> "live" | "idle" | "unknown"`. Locate the
  `Voice dictation` AXButton once at dictation start (cache the ref); classify by
  `AXDescription in {"Stop recording"}` OR a `recording*` entry in `AXDOMClassList`
  (match loosely — the hashed suffix is extension-internal). `unknown` when the button
  can't be read (tab switched, stale ref).
- **Start-confirmation:** after `keys.cmd_d()`, require `read_button_state() == "live"`
  within `dictation_start_ms` (poll on `tick()`); else retry Cmd+D once, then abort the turn
  with a distinct beep + `outcome="no_dictation"`. Removes the silent "Cmd+D didn't take →
  confusing timeout" path.
- **Liveness gate** (the §4 rework, already drawn): while `live`, reset `_last_activity` —
  no countdown, survives thinking pauses. On transition `live → idle`, off-ramp: stop
  observing, disarm, never send (same terminal as timeout). On `unknown`, fall back to the
  `disarm_s` silence timer (Phases 3/4 already harden that path).
- Keep `disarm_s` + a hard `max_arm_s` ceiling as the fallback backstop; add `max_arm_s` to
  `[timeouts]`.
- Supersede the BACKLOG "VAD-based off-ramp" entry — point it at this approach as the chosen
  path (button = the OS session's ground truth; VAD only approximates it).

**Tests:** fake AX button that (a) never goes live → start-confirmation aborts after retry;
(b) goes live then idle → off-ramp disarms without sending; (c) returns `unknown` → silence
timer still governs. Fake-clock throughout.

**DoD:** dictation start is confirmed, not assumed; a thinking pause no longer disarms while
blue; grey button is a definite off-ramp; §4 markers flip to verified.
**Risk:** medium (external AX contract). **Depends on:** Phases 1, 3. **Est:** medium-large.

---

## Phase 6 — `chotu --doctor` self-test (loud failure on extension churn)

**Gap:** hardcoded keycodes, textarea assumption, button description/DOM hashes — all break
silently on VS Code / Claude-extension updates and degrade to timeouts.

**Changes** ([__main__.py](../chotu/__main__.py)):
- Add `--doctor`: assert and report {VS Code running, focus gate passes, `AXManualAccessibility`
  sticks, focused element is the expected textarea, `Voice dictation` button located, button
  classifies as `idle` at rest}. Non-zero exit + human-readable diffs when a contract breaks.
- Document it in the README under Setup as the post-update sanity check.

**Tests:** `--doctor` against a fake system returns pass; each broken precondition yields a
distinct non-zero code + message.

**DoD:** an extension update that moves the AX contract fails `--doctor` loudly instead of
degrading at runtime. **Risk:** low. **Depends on:** Phase 5 (button locator). **Est:** small.

---

## Phase 7 — Smaller cleanups (fold into an adjacent phase's PR)

- **Config schema:** replace the `getattr(cfg, "fixups", None)` bag-access
  ([commands.py:126](../chotu/commands.py#L126)) with an explicit typed field + default;
  audit `config.py` for other optional-by-`getattr` keys.
- **Wake enqueue comment:** note at [wake.py:93](../chotu/wake.py#L93) that enqueuing on
  `score >= log_floor` (0.3, not `threshold`) is deliberate near-miss logging, and `tick()`
  drains sub-threshold events by design.
- **`disarm_s` already bumped 10→22** in the tracked `config.toml` (live user config is 30);
  once Phase 5 lands, drop it back to a sane backstop value (~15) since it's no longer the
  primary signal.

---

## Sequencing summary

```
Phase 0 (docs)  ──▶ shippable immediately, unblocks honest diagrams
Phase 1 (_transition) ──▶ foundation for 2,3,4
   ├─▶ Phase 2 (actions extraction)  ──▶ makes `acting` real
   ├─▶ Phase 3 (stale-ref robustness)
   │      └─▶ Phase 5 (button signal) ──▶ Phase 6 (--doctor)
   └─▶ Phase 4 (settle window)
Phase 7 folds into whichever PR touches the file.
```

Recommended first PR: **Phases 0 + 1** — zero-to-low risk, directly answer the "verified"
critique, and lay the transition-logging foundation everything else builds on.
