# hey-claude 🎙️ → Claude Code

Wake-word voice controller for driving the **Claude Code VS Code extension**
hands-free on macOS. Say **"chotu"**, dictate your prompt, end with a command word
("okay send") — and it submits. Offline, single-user, personal.

**Status:** v1 implementation. Design is fully specified and every integration
point is verified — see [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md),
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/BUILD-PLAN.md`](docs/BUILD-PLAN.md).

## How it works

```
"chotu"  →  open/focus VS Code + geofast workspace  →  Cmd+Esc (focus) → Cmd+D (dictate)
         →  you speak: "add a retry loop. okay send."
         →  chotu reads the box (AXObserver), sees the trailing "okay send"
         →  Cmd+D (stop) → backspaces "okay send" → Return
```

Only the wake word ("chotu") is a trained model. Every **command** is read from the
input box text and string-matched (Option A), so the command word is stripped before
the prompt reaches Claude. Reads are event-driven via macOS Accessibility; writes are
keystrokes (`osascript`). Details + diagrams in [ARCHITECTURE](docs/ARCHITECTURE.md).

## Setup

```bash
git clone <this repo> && cd hey-claude
./scripts/setup.sh        # creates .venv, installs deps, generates the LaunchAgent
```

Then grant permissions (one-time, **required** — the daemon is a new process):

1. **System Settings → Privacy & Security → Accessibility** → add your `.venv/bin/python`
   (keystrokes + reading the box).
2. **System Settings → Privacy & Security → Microphone** → add it too (wake listening).

chotu forces `AXManualAccessibility` on VS Code at startup so the input box becomes
readable — no VoiceOver needed.

### Train the "chotu" wake word

Until you train it, the daemon runs with a pretrained fallback phrase
(`wake.pretrained_fallback`, default `hey_jarvis`) so you can try it immediately.
To use "chotu", follow [`scripts/train_chotu.md`](scripts/train_chotu.md) and set
`wake.model` in `config.toml` to the resulting `.onnx`.

## Run

```bash
# Smoke-test the whole loop WITHOUT the wake word (arms once):
python -m chotu --once      # then dictate a prompt and say "okay send"

# Debug: print what chotu reads from the focused box:
python -m chotu --read

# Normal daemon (also auto-starts at login once the LaunchAgent is loaded):
python -m chotu
launchctl load ~/Library/LaunchAgents/com.hey-claude.chotu.plist
```

## Config

Everything lives in [`config.toml`](config.toml) (FR-5): wake phrase/model/threshold,
command words + disambiguation `prefix`, target workspace, timeouts, telemetry. Omit
any key to use its default.

## Telemetry & tuning

Every turn logs a structured JSONL event to `~/Library/Logs/hey-claude/` (redactable;
audio never stored). After some real use:

```bash
python scripts/stats.py    # false-trigger rate, command precision, p50/p95 latency, …
```

Use the numbers to tune the wake threshold, the command prefix, and timeouts — see
[ARCHITECTURE §6](docs/ARCHITECTURE.md#6-telemetry--tuning-detailed).

## Tests

```bash
pytest                 # unit suite — fast, no pyobjc, no VS Code needed
pytest -m integration  # live AX/keystroke smoke (needs a focused VS Code)
```

The correctness-critical logic (command match/strip, state machine, telemetry) is
pure and injected with fakes, so `pytest` is green without a GUI. See
[ARCHITECTURE §7](docs/ARCHITECTURE.md#7-testing-strategy).

## Troubleshooting

Hard-won from the first live ride (2026-07-06):

- **Wake word never fires / all scores 0.000** — an incompatible **onnxruntime**
  silently breaks openWakeWord (features compute, classifier outputs 0 on *everything*,
  even known-positive clips). Pinned to `>=1.16,<1.19`; if it regresses, verify with
  `pip show onnxruntime`. Debug with `CHOTU_WAKE_DEBUG=1 python -m chotu` → watch
  `~/Library/Logs/hey-claude/wake-debug.log` for the `max_score` heartbeat.
- **Silent mic (peak ~64)** — the process lacks Microphone permission (separate from
  the extension's). Run `python -m chotu --mic-check`; grant Microphone to your
  terminal / the venv python. On a **Mac mini there is no built-in mic** — a
  Bluetooth headset is your only input.
- **`overflow=True` / dropped audio** — bursty Bluetooth delivery; the callback+queue
  capture handles it (heartbeat should show `overflows=0`, ~25 chunks/2s).
- **Command word ends up in the sent prompt** — dictation writes "Okay. Send." with
  punctuation; the token-based matcher strips it. If a new command word misfires,
  check `box_pre_strip` in the telemetry.

## Layout

```
chotu/      config ports commands state telemetry bootstrap ax keys system wake __main__
scripts/    stats.py  setup.sh  train_chotu.md
tests/      unit suite + integration/
docs/       REQUIREMENTS · ARCHITECTURE · BUILD-PLAN
```
