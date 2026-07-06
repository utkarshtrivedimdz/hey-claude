# Plan — Button-as-truth dictation (event-driven) with feedback

**Status:** proposed (awaiting approval)
**Date:** 2026-07-06
**Supersedes:** the `Cmd+D` dictation toggle (keys.py).

## Problem (observed, reproduced)

hey-claude starts/stops dictation by sending **Cmd+D** and *assuming* it worked. Two live
failures trace to this:

1. **"Dictation randomly starts."** `Cmd+D` is also VS Code's *add-selection-to-next-match*
   (multi-cursor) shortcut, so normal editing toggled the dictation button — no wake word
   involved. (Silent-mic test confirmed the wake model is not the trigger: 40 s silence →
   peak 0.000.)
2. **"Detected → focused VS Code → dictation didn't start."** `Cmd+D` is an unreliable way
   to hit the button, and hey-claude logged `dictation started` on the *keystroke*, never
   checking reality. (Focus/raise is **not** the issue — VS Code comes forward fine.)

**Root cause:** dictation is driven by a colliding keystroke with no read-back of true
state. The mechanism is wrong; threshold/model tuning can't fix it.

## Verified on this machine (2026-07-06)

Probes (`scripts/ax_probe.py`, `ax_toggle_probe.py`, `ax_notify_probe.py`) against live VS Code:

- Button = `AXButton`, exposes **`AXPress`**; pressing toggles dictation reliably.
- **State is readable** from `AXDescription`: `Voice dictation` = OFF, `Stop recording` = ON
  (blue). `AXDOMClassList` gains `recording_…` when on.
- **State change emits an event:** toggling fires **`AXTitleChanged`** on the button
  element. → an **event-driven** design (no polling) is viable.

## Design — the button is the single source of truth

**Core principle (your requirement): `DICTATING` ⟺ the button is blue.** An `AXObserver`
on the button (`AXTitleChanged`) is the *authority* for entering and leaving `DICTATING`.
hey-claude only *requests* changes (AXPress); the **event** confirms them. No poll, no assume.

This is the same observer pattern already used for the textarea (`AXValueChanged`), so it
fits the architecture rather than bolting on.

### State model (states unchanged; `LEGAL` table unchanged)

`IDLE → ARMED → DICTATING → ACTING → IDLE`, with dictation transitions now event-gated:

- **`on_wake(score)`** (IDLE + accepted): → `ARMED`; bootstrap; `keys.cmd_esc()` (focus box);
  `observe_dictation(cb)` + `observe_box(cb)`; then read `ax.dictation_on()` **once**:
  - `None` (button not found) → **fail loud** (Invariant #5): `error` beep, `log.error`,
    resolve wake `False`, log turn `"error"`, → `IDLE`. No keystrokes / no Return.
  - `True` (already recording) → we are dictating now → `listening` beep → `DICTATING`.
  - `False` → `ax.press_dictation()` (request ON); **stay `ARMED`** — the button's
    `AXTitleChanged→ON` event drives the transition.
- **`on_dictation_change(is_on)`** [NEW event, from the button observer — ground truth]:
  - `ARMED` + `is_on` → `listening` beep → `DICTATING` (confirmed).
  - `DICTATING` + not `is_on` → dictation ended **externally** (user clicked the mic off, or
    macOS dropped it) → disarm → `IDLE` (never auto-sends). *This is new capability the
    old design couldn't see.*
  - `ACTING` + not `is_on` → expected (we pressed OFF to run the command) → ignore.
  - else → ignore (state-guarded, like `on_box_change`).
- **`on_box_change(text)`** (`DICTATING` only): match command → `ACTING`.
- **`tick()`**: `ARMED` too long (press produced no ON event) → arm-timeout backstop →
  `IDLE` (fail loud: dictation never started). `DICTATING` silence too long → press OFF →
  disarm.
- **`_act` / dispatch**: → `ACTING`; stop dictation (press OFF if on); perform send/cancel/
  stop; → `IDLE`.

Verification is now **implicit in the event**: we only ever enter `DICTATING` because the
button reported blue. The old `verify_timeout_ms` poll is deleted.

### Port surface — `hey_claude/ports.py` (`AXPort`)

```python
# dictation (button = truth)
def dictation_on(self) -> Optional[bool]: ...          # True=on, False=off, None=not found
def press_dictation(self) -> None: ...                 # unconditional AXPress (a request)
def observe_dictation(self, on_change: Callable[[bool], None]) -> bool: ...
def stop_observing_dictation(self) -> None: ...
# textarea observer renamed for symmetry (was start_observing/stop_observing)
def observe_box(self, on_change: Callable[[str], None]) -> bool: ...
def stop_observing_box(self) -> None: ...
```

### `hey_claude/ax.py` (RealAX)

- Extract a tiny internal `_Observer` holder `(observer, source, element, handler)` so the
  **box** and **dictation** observers coexist without duplicated teardown logic (the module
  currently hard-codes a single observer's fields). Clean refactor, no behavior change to
  the box path.
- `_find_element(role, descriptions)` — depth-capped tree walk; **reused** later by the
  backlog's press-dialog-buttons-by-name feature.
- `dictation_on()` — find button (desc ∈ {off,on}); return `desc == on_label`; `None` if
  not found.
- `press_dictation()` — find + `AXPress`; `log.warning` if not found.
- `observe_dictation(cb)` — attach an `AXObserver` for **`AXTitleChanged`** on the button;
  handler reads `AXDescription`, calls `cb(desc == on_label)`.

### `hey_claude/state.py` / `hey_claude/actions.py`

- Remove all three `cmd_d` calls (start, silence-stop, command-stop).
- Add the `on_dictation_change` event handler (above).
- `actions.perform()`: stop dictation via `ax.press_dictation()` guarded by
  `ax.dictation_on()` (only press if on), replacing `keys.cmd_d()`. Send/cancel/stop
  keystroke choreography unchanged.

### Feedback (your explicit ask: "is dictation actually on?")

- **Audible:** new `listening` beep (distinct sound, e.g. `Glass`) fires *only* on the
  ground-truth `→ DICTATING`; `error` (`Basso`) on the fail-loud path. A soft cue when the
  button goes OFF externally (reuse `stop`/`cancel` sound).
- **Log:** INFO on every confirmed ON/OFF with the source (`button_on`, `button_off_external`,
  `button_off_command`); ERROR when the button is missing.
- **Telemetry:** transition reasons above; `dictation_verified=True` on turns (we only reach
  `DICTATING` via truth).

### Config — `config.toml` + `hey_claude/config.py`

```toml
[dictation]
button_desc_off = "Voice dictation"   # AXDescription when OFF
button_desc_on  = "Stop recording"    # AXDescription when ON (blue/recording)
```

Typed fields, defaults, validation (non-empty). No poll timeout (event-driven). No
`getattr` bags.

### Remove the dead `Cmd+D` path (holistic — approved)

Delete `KeysPort.cmd_d`, `RealKeys.cmd_d`, `FakeKeys.cmd_d`, the `cmd_d` keymap entry, and
the `keys.py` module docstring reference. Confirmed no other caller.

## Tests

- **`tests/fakes.py`** — `FakeAX` gains `_dict_on`, `dictation_on()`, `press_dictation()`
  (flips state and, if observing, fires the dictation callback → simulates the real event),
  `observe_dictation`/`stop_observing_dictation`, `feed_dictation(is_on)` (external toggle),
  `dict_button_present=False` mode (→ `None`). Rename box observer methods. Drop `FakeKeys.cmd_d`.
- **`tests/test_state.py`** —
  - arm (button off): `keys.names()==["cmd_esc"]`, `press_dictation` called, state still
    `ARMED`; then `feed_dictation(True)` → `DICTATING` + `listening` beep.
  - arm (button already on): read → `DICTATING` directly.
  - `feed_dictation(False)` during `DICTATING` → `IDLE`, `"ret" not in keys` (never sends).
  - button missing → fail loud: `error` beep, `IDLE`, not observing, `"ret" not in keys`.
- **`tests/test_actions.py`** — replace the four `cmd_d` assertions (36/43/69/85): stop is
  now `press_dictation` on `FakeAX`, so `keys.names()` drops the leading `cmd_d`.
- **`tests/integration/`** — `-m integration` smoke: AXPress the real button, assert the
  `AXTitleChanged` callback delivers the flipped state (mirrors `ax_notify_probe`).

## Invariant compliance

- **#1 pure I/O-free:** `state.py`/`actions.py` depend on `AXPort`; unit suite runs with no
  pyobjc. ✓
- **#2 never auto-sends:** fail-loud and external-off paths emit no `ret`; asserted. ✓
- **#3 single transition chokepoint:** all changes go through `_transition`. ✓
- **#4 single-threaded:** the dictation observer delivers on the CFRunLoop (like the box
  observer); no new threads; no poll loop. ✓
- **#5 fail loud:** button missing / no ON event (arm-timeout) → ERROR + aborted turn. ✓

## Docs updated in the same change

- `docs/ARCHITECTURE.md` — dictation is an event-driven, button-truth toggle; add the
  `on_dictation_change` edge to the state diagram.
- `docs/BACKLOG.md` — check off the AXPress-dictation item; note `_find_element` now exists
  for press-by-name.
- `CLAUDE.md` — module table: `ax.py` owns the dictation button (drive + observe); `keys.py`
  no longer does dictation.

## Out of scope

- `raise_app` / focus gate — **not an issue** (confirmed); no change.
- Generic press-dialog-buttons-by-name — future backlog item; this plan leaves the reusable
  `_find_element` finder in place for it.

## Acceptance

1. Unit suite green (`.venv/bin/python -m pytest`).
2. Live: wake → `listening` beep + button turns blue (`DICTATING`) → speak → command →
   button off (`IDLE`). Manually clicking the mic off mid-turn disarms hey-claude. Normal editing
   `Cmd+D` no longer starts dictation.
3. Keep `scripts/wake_listen.py`, `ax_probe.py`, `ax_toggle_probe.py`, `ax_notify_probe.py`
   as committed diagnostics.
4. Restart the LaunchAgent on green.
