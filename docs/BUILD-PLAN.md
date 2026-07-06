# hey-claude — Build Plan (v1)

Companion to [`REQUIREMENTS.md`](REQUIREMENTS.md) (the *what*) and
[`ARCHITECTURE.md`](ARCHITECTURE.md) (diagrams + the concrete telemetry data model).
This doc is the *how*. Everything here is grounded in the 2026-07-06 feasibility
tests — the hard parts (AX read, event-driven observe, keystroke write) are proven.

## Locked decisions
- **Engine:** openWakeWord (train "hey-claude"). Fallback: `SFSpeechRecognizer` (Q1).
- **Surface:** headless LaunchAgent daemon + beeps + JSONL log (Q5).
- **Commands:** Option A — dictated into the box, then stripped (Q11b).
- **Reads:** event-driven `AXObserver`; **writes:** keystrokes (AX-set doesn't stick).
- **Lang:** Python 3.11 + pyobjc; keystrokes via `osascript` (or `CGEvent` later).

## Core loop (verified)
```
"hey-claude" (openWakeWord)
  → set AXManualAccessibility=true on VS Code   [startup, once]
  → bootstrap: open VS Code → geofast workspace → raise to front
  → focus-safety gate: frontmost==com.microsoft.VSCode && title~"geofast (Workspace)"
  → Cmd+Esc (focus Claude input)  →  Cmd+D (start dictation)
  → AXObserver on AXTextArea.AXValueChanged  (event-driven)
  → on each change: inspect trailing token
       → command word (prefix/pause-disambiguated)?
            → Cmd+D (stop dictation)
            → backspace-strip the command phrase
            → dispatch: send→Return | cancel→clear | stop→Esc
  → log the turn (FR-7) → idle
```

## Repo layout
```
hey-claude/                     (~/Documents/GitHub/hey-claude, local git)
├── hey_claude/
│   ├── __main__.py             # daemon entry: wire wake → state machine
│   ├── config.py               # load/validate config (FR-5)
│   ├── ax.py                   # AX layer: read AXValue, AXObserver (from tests)
│   ├── keys.py                 # keystroke primitives: Cmd+D/Esc, type, backspace, Return
│   ├── bootstrap.py            # open VS Code / workspace / focus-safety gate
│   ├── wake.py                 # openWakeWord listener → callback
│   ├── commands.py             # trailing-token match, disambiguation, strip, dispatch
│   ├── state.py                # FR-6 state machine + timeouts + self-trigger guard
│   └── telemetry.py            # FR-7 JSONL logging + stats
├── models/hey_claude.onnx           # trained wake-word model
├── config.toml                 # user config
├── scripts/stats.py            # derived metrics over the log
├── tests/
│   ├── test_commands.py        # golden match/strip table (ARCH §7.3)
│   ├── test_state.py           # transitions, timeouts, self-trigger (fake clock/ports)
│   ├── test_telemetry.py       # redaction, correction window
│   ├── test_stats.py           # metric math over fixtures
│   └── integration/            # @pytest.mark.integration — live VS Code, skipped by default
├── com.hey-claude.plist  # LaunchAgent
└── README.md                   # setup, permissions, config
```

**Testability rule (enables the unit suite):** `state.py`/`commands.py` depend on
`AXPort`/`KeysPort` **protocols**, not the concrete pyobjc/osascript modules — so
unit tests inject fakes and **never import pyobjc**. Full strategy + golden tables in
[`ARCHITECTURE.md` §7](ARCHITECTURE.md#7-testing-strategy).

## Phases (walking skeleton first)

### M0 — Scaffold
- Repo + venv; deps: `openwakeword`, `pyobjc-framework-ApplicationServices`,
  `pyobjc-framework-Cocoa`, `sounddevice`/`pyaudio`. Git init.

### M1 — Walking skeleton (no wake word yet) ⭐ prove the loop end-to-end
- `ax.py` + `keys.py` from the verified test code (below), behind `AXPort`/`KeysPort`.
- `commands.py` + a minimal `state.py`; a CLI trigger (`python -m hey_claude --once`) that
  runs: focus-safety gate → Cmd+Esc → Cmd+D → observe box → detect trailing "send" →
  strip → Return.
- **Tests:** land `test_commands.py` (golden table, ARCH §7.3) + `test_state.py`
  (send/cancel/stop/self-trigger) now — they need no VS Code.
- **Exit criterion:** a real prompt dictated + "send" submits it, command word not
  in the sent text. (This is the whole system minus the wake word.)

### M2 — Wake word
- Train "hey-claude" (openWakeWord synthetic-data: piper TTS → augment → train → onnx).
- `wake.py`: always-on listener, fires the M1 loop on detection. Threshold to config.

### M3 — Control layer (FR-6)
- `state.py`: idle→armed→dictating→acting with timeouts; self-trigger guard (ignore
  wake while dictating); `cancel`/`stop`; disarm-on-silence (never auto-send).
- Bootstrap cold-start path (launch VS Code if down) + "starting" cue.
- **Tests:** expand `test_state.py` — timeout/disarm cases with a **fake clock**;
  `test_commands.py` for `command_prefix` on/off; `bootstrap` focus-gate decision.

### M4 — Telemetry (FR-7)
- `telemetry.py`: per-turn JSONL (wake score, command, pre/post-strip, outcome,
  latency, errors) + implicit mis-fire flag (stop/cancel/scratch after an action).
- `scripts/stats.py`: false-trigger rate, command precision, p50/p95 latency.
- **Tests:** `test_telemetry.py` (redaction modes, correction-window linking) +
  `test_stats.py` (metric math over fixture JSONL).

### M5 — Package & permissions
- `config.toml` (FR-5) fully wired. LaunchAgent plist (starts at login, KeepAlive).
- README: **Microphone + Accessibility grants for the daemon** (NFR-7 — it needs its
  *own* grant, won't inherit), and the AXManualAccessibility startup step.

### M6 — Battle-test & tune
- Run it on the couch. Use FR-7 logs to settle: wake threshold, Q7 sentence-done
  mode, Q11b prefix-vs-pause, arm/confirm timeouts. Tune, don't guess.

## Verified snippets to start from (tested 2026-07-06)

**Read box + force a11y** (`ax.py`):
```python
from ApplicationServices import (AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue, AXUIElementSetAttributeValue)
from AppKit import NSWorkspace
app = NSWorkspace.sharedWorkspace().frontmostApplication()
ax  = AXUIElementCreateApplication(app.processIdentifier())
AXUIElementSetAttributeValue(ax, "AXManualAccessibility", True)   # unlocks the tree
err, fe  = AXUIElementCopyAttributeValue(ax, "AXFocusedUIElement", None)
err, val = AXUIElementCopyAttributeValue(fe, "AXValue", None)     # -> box text
# NB: empty box returns its PLACEHOLDER (e.g. "Queue another message…"); treat as "".
```

**Event-driven observe** (`ax.py`) — 33/33 events, ref stable within a turn:
```python
import objc
from ApplicationServices import (AXObserverCreate, AXObserverAddNotification,
    AXObserverGetRunLoopSource)
from CoreFoundation import (CFRunLoopGetCurrent, CFRunLoopAddSource,
    CFRunLoopRunInMode, kCFRunLoopDefaultMode)

@objc.callbackFor(AXObserverCreate)                 # REQUIRED wrapper
def cb(observer, element, notification, refcon):
    err, val = AXUIElementCopyAttributeValue(element, "AXValue", None)
    ...  # inspect trailing token
err, observer = AXObserverCreate(pid, cb, None)
AXObserverAddNotification(observer, textarea, "AXValueChanged", None)
CFRunLoopAddSource(CFRunLoopGetCurrent(), AXObserverGetRunLoopSource(observer),
                   kCFRunLoopDefaultMode)
# re-register on focus each new turn (a sent message re-renders the panel)
```

**Keystrokes** (`keys.py`) — write+strip verified (`abc def`→backspace→`abc `):
```python
# via osascript: type, strip, submit
osa('keystroke "abc def"')            # type
osa('repeat 3 times\n key code 51\nend repeat')  # backspace ×3 (key 51 = delete)
osa('key code 36')                    # Return
# Cmd+D = key code 2 + command; Cmd+Esc = key code 53 + command
```

**Focus-safety gate** (`bootstrap.py`):
```python
front = NSWorkspace.sharedWorkspace().frontmostApplication()
ok = (front.bundleIdentifier() == "com.microsoft.VSCode"
      and "geofast (Workspace)" in window_title(ax))
if not ok: raise AbortInject   # raise VS Code / error-cue instead of firing keys
```

## Environment facts (verified)
- `code` CLI **not** on PATH → use `open -n -a "Visual Studio Code"` / `open <ws>`.
- Workspace file: `/Volumes/GeofastStorage/GitHub/geofast.code-workspace` (multi-root).
- `AXIsProcessTrusted()` True for a shell-spawned process; the **LaunchAgent daemon
  needs its own Accessibility grant** — verify early in M5.
- Cmd+D is also VS Code's native "select next occurrence" → **Cmd+Esc must precede
  Cmd+D** so focus is in the Claude input.

## Tuning items deferred to data (M6, via FR-7)
Q7 (sentence-done mode), Q11b (prefix vs pause), wake threshold, arm/confirm
timeouts. All resolvable from the log after real use — see REQUIREMENTS FR-7.
