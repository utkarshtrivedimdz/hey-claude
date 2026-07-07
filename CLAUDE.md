# CLAUDE.md — hey-claude

Wake-word voice controller for **Claude Code in VS Code**, on macOS. Say the wake word
("hey jarvis" by default; no trained "hey claude" model yet), dictate a prompt, end with
a command word ("okay send" / "okay cancel" / "okay stop");
the daemon strips the command and drives the Claude input box. Runs as a LaunchAgent.

## Architecture (hexagonal — read this first)

Pure control logic is isolated behind **ports** (protocols in [`hey_claude/ports.py`](hey_claude/ports.py))
so it's unit-testable with fakes and **never imports pyobjc**. Only three modules touch
the outside world.

| module | role | pure? |
|---|---|---|
| [`hey_claude/state.py`](hey_claude/state.py) | FR-6 state machine: `IDLE→ARMED→DICTATING→IDLE`. Owns all sequencing + dispatch. | **pure** |
| [`hey_claude/commands.py`](hey_claude/commands.py) | trailing-token command match + strip + fixups. The most-tested module. | **pure** |
| [`hey_claude/bootstrap.py`](hey_claude/bootstrap.py) | launch/open/raise VS Code + focus-safety gate over a `SystemPort`. | **pure** |
| [`hey_claude/telemetry.py`](hey_claude/telemetry.py) | append-only JSONL tuning events (redactable). | **pure** |
| [`hey_claude/config.py`](hey_claude/config.py) | `config.toml` → `Config` dataclass, with defaults + validation. | **pure** |
| [`hey_claude/ax.py`](hey_claude/ax.py) | Accessibility: read box (`AXObserver` on `AXValueChanged`); drive + observe the Voice-dictation button (`AXPress` + `AXTitleChanged` = dictation ground truth). | pyobjc |
| [`hey_claude/keys.py`](hey_claude/keys.py) | keystroke injection via `osascript` (Esc/type/⌫/⏎; **not** dictation — that's an AXPress). | osascript |
| [`hey_claude/system.py`](hey_claude/system.py) | frontmost / launch / raise / window title. | pyobjc |
| [`hey_claude/wake.py`](hey_claude/wake.py) | openWakeWord listener on its own thread. | audio |
| [`hey_claude/menubar.py`](hey_claude/menubar.py) | `NSStatusItem` toggle: click mutes/unmutes wake listening (waveform icon). Needs `NSApp.run()`. | pyobjc |
| [`hey_claude/log.py`](hey_claude/log.py) | human-readable debug stream (stdlib `logging`). | — |
| [`hey_claude/__main__.py`](hey_claude/__main__.py) | wires everything on a CFRunLoop (daemon runs it under `NSApp.run()` for the menu bar). | — |

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
[`hey_claude/log.py`](hey_claude/log.py) attaches stderr + a rolling file at
`~/Library/Logs/hey-claude/hey-claude.log`. Level convention:

- **DEBUG** — every keystroke, box change, AX callback, transition, focus-gate poll.
- **INFO** — the narrative: wake→arm→dictate→sent/cancel/timeout, bootstrap result.
- **WARNING** — degraded-but-recoverable: illegal transition, stale AX ref, empty-box abort, mic overflow.
- **ERROR** — turn-aborting failure: bootstrap fail, observer setup fail, osascript failure.
- **CRITICAL** — daemon-deaf: the wake thread crashed, or the mic dropped (daemon stays up, menu bar flips to not-listening; click to self-heal).

Enable verbose: `python -m hey_claude --debug` or `HEY_CLAUDE_DEBUG=1`. Default is INFO.

## Working here

```bash
.venv/bin/python -m pytest        # unit suite — fast, no pyobjc/VS Code (note: use the venv python directly)
.venv/bin/python -m pytest -m integration   # live AX/keystroke smoke (needs focused VS Code)
python -m hey_claude --once            # arm once WITHOUT the wake word (end-to-end smoke)
python -m hey_claude --read            # print what hey-claude reads from the focused box
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
