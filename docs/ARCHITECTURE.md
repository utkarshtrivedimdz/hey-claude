# hey-claude — Architecture & Design

Diagrams and the concrete data model behind [`REQUIREMENTS.md`](REQUIREMENTS.md) /
[`BUILD-PLAN.md`](BUILD-PLAN.md). Every mechanism here is verified (2026-07-06 tests).
Diagrams are Mermaid (render in VS Code with the Mermaid extension, and on GitHub).

---

## 1. Component diagram

```mermaid
graph TD
  MIC(("microphone")):::ext
  subgraph daemon["chotu daemon (LaunchAgent)"]
    W["wake.py<br/>openWakeWord"]
    SM["state.py<br/>state machine (FR-6)"]
    BS["bootstrap.py<br/>open + focus-safety gate"]
    CMD["commands.py<br/>match / disambiguate / strip"]
    AX["ax.py<br/>AX read + AXObserver"]
    KEYS["keys.py<br/>keystroke primitives"]
    TEL["telemetry.py<br/>JSONL logger"]
    CFG["config.py"]
  end
  VSC["VS Code<br/>Claude extension"]:::ext
  LOG[("events.jsonl")]:::store
  STATS["scripts/stats.py"]

  MIC --> W
  W --> SM
  SM --> BS
  SM --> CMD
  SM --> TEL
  CMD --> AX
  CMD --> KEYS
  BS --> KEYS
  BS --> AX
  AX <-->|"AXValue / AXObserver<br/>(needs AXManualAccessibility)"| VSC
  KEYS -->|"Cmd+D/Esc, type, ⌫, ⏎"| VSC
  CFG -.-> W
  CFG -.-> SM
  CFG -.-> CMD
  CFG -.-> TEL
  TEL --> LOG
  STATS --> LOG

  classDef ext fill:#e8e8e8,stroke:#888,color:#000;
  classDef store fill:#d9edf7,stroke:#3a87ad,color:#000;
```

`ax.py` and `keys.py` are the only modules that touch VS Code — everything else is
pure logic, which keeps the state machine and command logic unit-testable without a
running editor.

---

## 2. Class diagram (UML)

```mermaid
classDiagram
  class Daemon {
    +run()
  }
  class WakeListener {
    +threshold: float
    +start(on_wake)
    -_score_loop()
  }
  class StateMachine {
    +state: State
    +on_wake(score)
    +on_box_change(text)
    +tick() timeouts
    -_to(state)
  }
  class Bootstrap {
    +ensure_ready() bool
    +focus_safe() bool
    -_raise_vscode()
  }
  class AX {
    +read_box() str
    +observe(element, on_change)
    +reregister_on_focus()
    +set_manual_a11y()
  }
  class Keys {
    +cmd_d()
    +cmd_esc()
    +type(s: str)
    +backspace(n: int)
    +ret()
    +clear() cmdA_delete
  }
  class Commands {
    +match(text) Match
    +strip_len(text, m) int
    +dispatch(m)
  }
  class Telemetry {
    +log_wake(rec)
    +log_turn(rec)
    +log_correction(turn_id, signal, dt)
  }
  class Config {
    +wake_phrase
    +command_words
    +command_prefix
    +timeouts
    +telemetry
  }

  Daemon --> WakeListener
  Daemon --> StateMachine
  StateMachine --> Bootstrap
  StateMachine --> Commands
  StateMachine --> Telemetry
  Commands --> AX
  Commands --> Keys
  Bootstrap --> Keys
  Bootstrap --> AX
  WakeListener ..> Config
  StateMachine ..> Config
  Commands ..> Config
  Telemetry ..> Config
```

---

## 3. Runtime sequence — happy path (one turn)

```mermaid
sequenceDiagram
  autonumber
  actor U as User
  participant W as wake.py
  participant SM as state.py
  participant BS as bootstrap.py
  participant AX as ax.py
  participant K as keys.py
  participant VS as VS Code
  participant T as telemetry

  U->>W: say "chotu"
  W->>SM: on_wake(score=0.72)
  SM->>T: log_wake(accepted, score)
  Note over SM: idle → armed
  SM->>BS: ensure_ready()
  BS->>VS: open -n -a / raise
  BS->>BS: focus_safe()? bundle+title
  BS->>K: Cmd+Esc (focus Claude input)
  SM->>K: Cmd+D (start dictation)
  Note over SM: armed → dictating (wake now ignored)
  SM->>AX: observe(textarea, on_change)
  AX->>VS: AXObserver(AXValueChanged)
  U->>VS: "add retry loop. okay send"
  loop each transcription chunk
    VS-->>AX: value changed
    AX-->>SM: on_box_change(text)
    SM->>SM: trailing token a command? prefix ok?
  end
  Note over SM: "okay send" matched → dictating → acting
  SM->>K: Cmd+D (stop dictation)
  SM->>K: backspace × len("okay send")
  SM->>K: Return
  SM->>T: log_turn(sent, latency, pre/post-strip)
  Note over SM: acting → idle
```

## 3a. Runtime sequence — cancel / mis-fire (implicit feedback)

```mermaid
sequenceDiagram
  autonumber
  actor U as User
  participant SM as state.py
  participant K as keys.py
  participant VS as VS Code
  participant T as telemetry

  Note over SM: a turn just ended: outcome=sent (turn_id=abc)
  U->>SM: stop / cancel / scratch (within N seconds)
  SM->>K: Esc
  SM->>T: log_correction(turn_id=abc, signal="stop", within_ms=2400)
  Note over T: turn abc flagged inferred=misfire<br/>→ training signal for Q11b / threshold
```

---

## 4. State machine (FR-6)

```mermaid
stateDiagram-v2
  [*] --> idle
  idle --> armed: wake "chotu" (≥ threshold)
  armed --> idle: bootstrap fail / focus-gate abort / arm-timeout
  armed --> dictating: Cmd+Esc → Cmd+D
  dictating --> dictating: box value-changed (observe)
  dictating --> acting: trailing command word (disambiguated)
  dictating --> idle: silence timeout — DISARM, never sends
  acting --> idle: strip + dispatch (send / cancel / stop)
  note right of dictating
    wake word IGNORED here
    (self-trigger guard, FR-1)
  end note
```

---

## 5. Command processing (flowchart)

```mermaid
flowchart TD
  E["AXValueChanged event"] --> R["read AXValue"]
  R --> P{"placeholder / empty?"}
  P -- yes --> WAIT["keep waiting"]
  P -- no --> TT["extract trailing token(s)"]
  TT --> M{"matches a command word?"}
  M -- no --> WAIT
  M -- yes --> PF{"prefix/pause satisfied? (Q11b)"}
  PF -- no --> WAIT
  PF -- yes --> STOP["Cmd+D — stop dictation"]
  STOP --> ACT{"which command"}
  ACT -- send --> S1["backspace-strip command phrase"] --> S2["Return"] --> L["log_turn"]
  ACT -- cancel --> C1["Cmd+A + Delete (clear)"] --> L
  ACT -- stop --> X1["Esc"] --> L
  L --> I["idle"]
```

---

## 6. Telemetry & tuning (detailed)

The point of telemetry is a **closed loop**: real sessions produce events → `stats.py`
derives metrics → metrics tune config (threshold, prefix, timeouts) → better behavior.
This is what makes the heuristics battle-tested instead of guessed.

### 6.1 Data flow

```mermaid
flowchart LR
  SM["state machine"] -->|"wake / wake_reject"| LOG[("events.jsonl")]
  SM -->|"turn"| LOG
  UF["stop/cancel/scratch<br/>shortly after a turn"] -->|"correction"| LOG
  LOG --> STATS["stats.py"]
  STATS --> M1["false-trigger rate"]
  STATS --> M2["command precision"]
  STATS --> M3["p50 / p95 latency"]
  STATS --> M4["wake-score sweep"]
  M1 & M2 & M3 & M4 --> TUNE["tune config:<br/>threshold · prefix · timeouts · Q7 mode"]
  TUNE -.->|"edit config.toml"| SM
```

### 6.2 Record types (append-only JSONL, one object per line)

Three event types share a common envelope. `schema` lets us evolve the format.

**Common envelope**
| field | type | notes |
|---|---|---|
| `schema` | int | format version (start `1`) |
| `ts` | string | ISO-8601 UTC, ms precision |
| `event` | enum | `wake` \| `turn` \| `correction` |
| `session_id` | string | per daemon start (groups a couch session) |

**`wake`** — every wake decision, *including near-misses & false accepts*
```json
{ "schema":1, "ts":"2026-07-06T21:03:10.101Z", "event":"wake", "session_id":"s_8f2",
  "score":0.72, "threshold":0.50, "accepted":true,
  "followed_through":true }        // false ⇒ armed then arm-timeout = false accept
```
Near-misses (below trigger but above a `log_floor`) are logged with
`accepted:false` so the score distribution is complete for threshold tuning.

**`turn`** — one completed (or aborted) interaction
```json
{ "schema":1, "ts":"2026-07-06T21:03:16.842Z", "event":"turn", "session_id":"s_8f2",
  "turn_id":"t_abc",
  "warm":true,
  "bootstrap":{ "cold_start":false, "ms":38, "focus_gate":"pass" },
  "dictation":{ "sentence_done":"trailing_word", "chars":84, "ms":6210 },
  "command":{ "matched":"send", "prefix":"okay",
              "box_pre_strip":"add retry loop okay send",
              "box_post_strip":"add retry loop", "strip_chars":10 },
  "outcome":"sent",                // sent | cancelled | stopped | timeout | error
  "latency_ms":{ "wake_to_action":780 },
  "errors":[] }
```

**`correction`** — implicit feedback, emitted async, linked by `turn_id`
```json
{ "schema":1, "ts":"2026-07-06T21:03:19.242Z", "event":"correction", "session_id":"s_8f2",
  "turn_id":"t_abc", "signal":"stop", "within_ms":2400, "inferred":"misfire" }
```
Rule: a `stop`/`cancel`/`scratch` (or an immediate re-`chotu` redo) within
`correction_window_ms` of a `sent` turn ⇒ that turn was probably a mistake.

### 6.3 Redaction (`store_prompt_text`)
`box_pre_strip` / `box_post_strip` are the only sensitive fields. The config knob
transforms them at write time:
| value | stored as |
|---|---|
| `full` | verbatim text |
| `hash` | `sha256(text)[:12]` (dedupe/repeat detection, not readable) |
| `length_only` | `{"len": 24}` |
| `off` | field omitted |
Audio is **never** written under any setting (NFR-1).

### 6.4 Retention & rotation
- One file per day: `~/Library/Logs/hey-claude/events-YYYY-MM-DD.jsonl`.
- Delete files older than `retention_days` on startup.
- Append-only; never rewritten (corrections are new lines, joined at analysis time).

### 6.5 Metrics (`scripts/stats.py`)
| metric | definition | tunes |
|---|---|---|
| **false-trigger rate** | `wake{accepted,!followed_through}` ÷ all `accepted` wakes | wake `threshold` |
| **wake-score sweep** | histogram of scores split by `followed_through` | optimal `threshold` |
| **command precision** | `1 − (misfired sends ÷ sends)`, misfire via `correction` | `command_prefix`, Q11b |
| **latency p50/p95** | `wake_to_action` over `warm` turns | perf regressions (NFR-4) |
| **cold-start rate/ms** | share of turns with `cold_start:true` + their ms | UX expectation |
| **disarm rate** | `dictating → idle` silence timeouts ÷ arms | arm/silence timeouts |
| **strip integrity** | sends whose `correction.within_ms` is tiny | strip/disambiguation bugs |

### 6.6 The tuning loop in practice
After ~a week of couch use: run `stats.py`. If false-trigger rate is high →
raise `threshold` (the sweep shows where). If command precision is low → the
`box_pre_strip` of misfired turns shows *why* (e.g. prompts ending in "send") →
tighten `command_prefix` or switch Q11b to pause-delimited, or fall back to
Option B. Every knob has a metric that points at it.

---

## 7. Testing strategy

### 7.1 What makes it testable
The correctness-critical logic (command match/strip, state transitions, telemetry)
is **pure** — it never calls pyobjc or presses a key. That's enforced by a design
rule: `state.py` and `commands.py` depend on **ports** (protocols) `AXPort` /
`KeysPort`, not on `ax.py` / `keys.py` directly. Tests inject `FakeAX`
(scriptable box-value sequence) and `FakeKeys` (records the ops emitted). So the
unit path **never imports pyobjc** — it runs anywhere, fast, in CI.

```mermaid
graph TD
  A["Unit — fast, no I/O, no pyobjc<br/>commands · state · config · telemetry · stats"]
  B["Integration — macOS, semi-manual<br/>ax.py + keys.py vs live VS Code"]
  C["Acceptance — REQUIREMENTS §9<br/>full couch run + FR-7 log review"]
  A --> B --> C
```

### 7.2 Unit targets
| module | test focus | how |
|---|---|---|
| `commands.py` | trailing-token match, prefix/pause disambiguation, `strip_len` | golden table (7.3) |
| `state.py` | transitions, timeouts, self-trigger guard, never-auto-send | event-sequence table + fake clock |
| `telemetry.py` | record shape, redaction modes, correction-window linking | assert emitted dicts |
| `config.py` | defaults, validation, bad-value handling | parametrized |
| `stats.py` | metric math over fixture JSONL | golden fixtures |
| `bootstrap.py` | focus-safety gate decision | fake frontmost bundle/title |
| `wake.py` | threshold + debounce over a synthetic score stream | fake scores |

### 7.3 Golden table — command match + strip (highest-value tests)
The riskiest logic; enumerate the traps explicitly.

| box text (dictated) | `command_prefix` | → matched | strip_chars | post-strip | why |
|---|---|---|---|---|---|
| `add retry loop okay send` | `okay` | `send` | 10 | `add retry loop` | prefix present |
| `add retry loop send` | `okay` | — | — | — | no prefix ⇒ **not** a command |
| `remind me to send the invoice` | `okay` | — | — | — | "send" mid-sentence, no prefix |
| `fix the bug okay cancel` | `okay` | `cancel` | — | *(clear)* | cancel path |
| `okay stop` | `okay` | `stop` | — | — | stop path |
| `add retry loop send` | `""` | `send` | 5 | `add retry loop` | bare-trailing-word mode |
| `remind me to send the invoice` | `""` | — | — | — | "send" not the final token |
| `Queue another message…` | any | — | — | — | placeholder ⇒ empty |
| `` (empty) | any | — | — | — | empty box |
| `Send.` / `SEND` | `""` | `send` | — | — | case/punct-insensitive final token |

```python
# tests/test_commands.py  (illustrative)
import pytest
from chotu.commands import Commands

@pytest.mark.parametrize("text,prefix,cmd,post", [
    ("add retry loop okay send", "okay", "send", "add retry loop"),
    ("add retry loop send",      "okay", None,   None),
    ("remind me to send the invoice", "okay", None, None),
])
def test_match_and_strip(text, prefix, cmd, post):
    c = Commands(prefix=prefix, words={"send":["send","okay send"]})
    m = c.match(text)
    assert (m.command if m else None) == cmd
    if cmd == "send":
        assert text[:len(text)-c.strip_len(text, m)].rstrip() == post
```

### 7.4 State-machine table tests (fake clock, fake ports)
Feed an event sequence, assert the state trajectory **and** the ops emitted to
`FakeKeys`:
- `[wake(0.7)]` → `armed`, bootstrap invoked.
- `[wake, box("hi okay send")]` → `acting`; FakeKeys ops = `[Cmd+D, ⌫×N, Return]`.
- `[wake, wake]` (2nd during dictating) → still `dictating` (**self-trigger ignored**).
- `[wake, tick(> silence_timeout)]` → `idle`, **no Return emitted** (never auto-sends).
- `[wake, box("… okay cancel")]` → `idle`; ops include `Cmd+A,Delete`, **no Return**.

### 7.5 Telemetry tests
- Redaction: `store_prompt_text=hash` ⇒ field is 12-hex; `off` ⇒ field absent;
  `length_only` ⇒ `{"len":N}`.
- Correction: `stop` at `within_ms < window` ⇒ `correction{inferred:"misfire"}`;
  at `> window` ⇒ **no** correction.
- `stats.py`: fixture of 10 accepted wakes, 2 not-followed-through ⇒
  false-trigger rate `0.2`; p50/p95 over known latencies.

### 7.6 Integration tier (macOS, semi-manual)
The already-written probes are the seeds: the `AXObserver` test and the
type→backspace→read test become `tests/integration/` smoke tests, marked
`@pytest.mark.integration` and **skipped by default** (need a live focused VS Code
box). Run locally before a release; they assert the AX layer still reads and the
keymap still lands.

### 7.7 Tooling & CI
- **pytest** with markers: `unit` (default), `integration`, `manual`.
- **Fake clock** (inject a `now()` callable) for timeout tests — no real sleeps.
- `tmp_path` fixtures for JSONL round-trips.
- CI (even just a local pre-push hook) runs `pytest -m unit` — green without a
  Mac GUI because the unit path never imports pyobjc (7.1). Integration/manual are
  developer-run.
- Per-milestone: **M1** lands the `commands` + `state` unit suites alongside the
  walking skeleton; each later milestone adds its module's tests before wiring.
