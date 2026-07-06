# CLAUDE.md — hey-claude (`chotu`)

Wake-word voice controller for **Claude Code in VS Code**, on macOS. Say "chotu",
dictate a prompt, end with a command word ("okay send" / "okay cancel" / "okay stop");
the daemon strips the command and drives the Claude input box. Runs as a LaunchAgent.

## Architecture (hexagonal — read this first)

Pure control logic is isolated behind **ports** (protocols in [`chotu/ports.py`](chotu/ports.py))
so it's unit-testable with fakes and **never imports pyobjc**. Only three modules touch
the outside world.

| module | role | pure? |
|---|---|---|
| [`chotu/state.py`](chotu/state.py) | FR-6 state machine: `IDLE→ARMED→DICTATING→IDLE`. Owns all sequencing + dispatch. | **pure** |
| [`chotu/commands.py`](chotu/commands.py) | trailing-token command match + strip + fixups. The most-tested module. | **pure** |
| [`chotu/bootstrap.py`](chotu/bootstrap.py) | launch/open/raise VS Code + focus-safety gate over a `SystemPort`. | **pure** |
| [`chotu/telemetry.py`](chotu/telemetry.py) | append-only JSONL tuning events (redactable). | **pure** |
| [`chotu/config.py`](chotu/config.py) | `config.toml` → `Config` dataclass, with defaults + validation. | **pure** |
| [`chotu/ax.py`](chotu/ax.py) | Accessibility: read box, `AXObserver` on `AXValueChanged`. | pyobjc |
| [`chotu/keys.py`](chotu/keys.py) | keystroke injection via `osascript`. | osascript |
| [`chotu/system.py`](chotu/system.py) | frontmost / launch / raise / window title. | pyobjc |
| [`chotu/wake.py`](chotu/wake.py) | openWakeWord listener on its own thread. | audio |
| [`chotu/log.py`](chotu/log.py) | human-readable debug stream (stdlib `logging`). | — |
| [`chotu/__main__.py`](chotu/__main__.py) | wires everything on a CFRunLoop. | — |

`StateMachine` holds `keys`/`ax`/`commands`/`bootstrap`/`telemetry`; `Bootstrap` holds
only `system`. The full picture is [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (diagrams;
elements tagged **«Phase N»** are target state from the hardening plan, not yet built).

## Invariants (don't break these)

1. **Pure modules stay I/O-free.** `state.py` / `commands.py` depend on ports, never on
   `ax`/`keys`/`system` concretely. The unit suite must run with no pyobjc, no VS Code.
   (Logging via stdlib `logging` is the one allowed cross-cutting exception — it never
   touches VS Code and is a no-op until `log.configure()` runs.)
2. **Never auto-sends.** Every disarm/timeout path ends the turn **without** emitting
   Return. Only an explicit "send" command submits. Tests assert `"ret" not in keys`.
3. **Single transition chokepoint.** All state changes go through `StateMachine._transition`,
   which asserts the edge against the `LEGAL` table and logs a `state_transition` event.
   No raw `self.state = …` anywhere except the initial assignment in `__init__`.
4. **Single-threaded state machine.** All mutations happen on the CFRunLoop. The wake
   thread only enqueues scores; an `NSTimer` (`tick()`, 0.12 s) drains them. Don't add
   runtime threads; `tick()` is the only polling budget.
5. **Brittle external contracts fail loud.** Keycodes, the textarea assumption, AX
   descriptions/DOM classes break silently on VS Code / Claude-extension updates — log
   at WARNING/ERROR, never degrade silently.

## Logging (`logger.debug/info/warning/error/critical`)

Human-readable "watch what's happening" stream — **separate** from telemetry (which is
structured tuning data, not for watching). Each module uses `logging.getLogger(__name__)`;
[`chotu/log.py`](chotu/log.py) attaches stderr + a rolling file at
`~/Library/Logs/hey-claude/chotu.log`. Level convention:

- **DEBUG** — every keystroke, box change, AX callback, transition, focus-gate poll.
- **INFO** — the narrative: wake→arm→dictate→sent/cancel/timeout, bootstrap result.
- **WARNING** — degraded-but-recoverable: illegal transition, stale AX ref, empty-box abort, mic overflow.
- **ERROR** — turn-aborting failure: bootstrap fail, observer setup fail, osascript failure.
- **CRITICAL** — daemon-deaf: the wake thread crashed.

Enable verbose: `python -m chotu --debug` or `CHOTU_DEBUG=1`. Default is INFO.

## Working here

```bash
.venv/bin/python -m pytest        # unit suite — fast, no pyobjc/VS Code (note: use the venv python directly)
.venv/bin/python -m pytest -m integration   # live AX/keystroke smoke (needs focused VS Code)
python -m chotu --once            # arm once WITHOUT the wake word (end-to-end smoke)
python -m chotu --read            # print what chotu reads from the focused box
```

- **`source .venv/bin/activate` may not persist** in a non-interactive shell — prefer
  `.venv/bin/python` directly.
- **Config-driven:** new tunables go in `config.toml` under a typed field in `config.py`
  (avoid `getattr(cfg, …)` bags), with a default and validation.
- **Docs move with code.** Behavior changes update the relevant `docs/ARCHITECTURE.md`
  diagram in the same change; forward-looking design carries a `«Phase N»` marker.
- **Roadmap:** [`docs/HARDENING-PLAN.md`](docs/HARDENING-PLAN.md) — phased, each phase
  independently shippable and leaves the tree green.

## When you make a mistake

If a mistake suggests a missing rule, add it here. Keep this file focused on invariants
and gotchas — architecture detail lives in `docs/ARCHITECTURE.md`.
