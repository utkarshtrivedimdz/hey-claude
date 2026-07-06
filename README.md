# hey-claude 🎙️ → Claude Code

Hands-free **voice control for the Claude Code VS Code extension** on macOS. Say the
wake word (**"hey jarvis"** by default — see note below), dictate your prompt, and end
with a spoken command ("okay send") — hey-claude focuses VS Code, drives the Claude input
box, strips the command, and submits. Offline, single-user, personal; runs as a
background LaunchAgent so it's always listening.

> **Wake word:** the assistant is *hey-claude*, but the spoken trigger is currently
> **"hey jarvis"** (openWakeWord's pretrained phrase). There's no pretrained "hey claude"
> model — say "hey jarvis" until you [train a custom one](scripts/train-wake-word.md).

**Status:** v1 shipped and in daily use; hardening ongoing (see
[`docs/BACKLOG.md`](docs/BACKLOG.md)). Design + verified integration points:
[`REQUIREMENTS`](docs/REQUIREMENTS.md) · [`ARCHITECTURE`](docs/ARCHITECTURE.md) ·
[`BUILD-PLAN`](docs/BUILD-PLAN.md).

## Why hey-claude (vs. plain dictation / hotkey tools)

- **Truly hands-free** — no hotkey, no clicking into a box. Say the wake word ("hey jarvis"
  by default) from *any* app and it opens/raises VS Code, focuses the Claude input, and
  starts the mic for you.
- **Knows Claude's actions** — you speak "send", "cancel", "clear", "stop"; generic
  dictation can only type text, it can't submit, interrupt, or wipe the box.
- **Won't send by accident** — it types your words but only *you* trigger the submit; a
  pause or dropped mic never fires a message.
- **Private & offline** — wake detection runs locally; nothing is streamed to a cloud
  speech service.
- **Fixes what it mishears** — auto-corrects recurring slips (e.g. "clod" → "Claude")
  before sending.

## What it does

- **Wake-word activation** — an always-listening [openWakeWord](https://github.com/dscripka/openWakeWord)
  model arms on the wake word (the pretrained "hey jarvis" until you train a custom
  "hey claude"). Nothing leaves the machine.
- **Focus safety** — on wake it launches/raises VS Code and opens your target
  `.code-workspace`, then *verifies* that window is frontmost before any keystroke — so
  it never types into an unrelated window. Works from any app (raises VS Code via
  LaunchServices).
- **Voice dictation** — starts macOS dictation by pressing the Claude panel's
  Voice-dictation button through Accessibility, and reads that button's state as the
  ground truth for whether it's recording (no fragile keystroke shortcut).
- **Spoken commands** — end a prompt with a prefixed command word and hey-claude acts:
  **"okay send"** submits · **"okay cancel"** / **"clear"** wipes the box ·
  **"okay stop"** interrupts Claude. The command word is stripped so it never reaches Claude.
- **Never auto-sends** — a turn submits *only* on an explicit "send"; any mic-off,
  timeout, or external interruption ends the turn silently.
- **Dictation fixups** — a substitution map corrects common mishearings
  (e.g. "clod code" → "Claude Code") before the prompt is sent.
- **Mic-drop resilience** — if your Bluetooth mic disconnects mid-session, hey-claude stops
  cleanly instead of running deaf; reconnect and run `hey-claude-restart` to resume.
- **Telemetry for tuning** — every turn appends a structured, redactable JSONL event
  (audio is never stored) so you can tune the wake threshold and command precision.

## How it works

```
"hey jarvis"  →  launch/raise VS Code + open target workspace  →  verify frontmost (focus gate)
         →  Cmd+Esc (focus Claude input) → AXPress the Voice-dictation button (start mic)
         →  you speak: "add a retry loop. okay send."
         →  hey-claude watches the box via AXObserver, sees the trailing "okay send"
         →  strip "okay send" → Return  (then mic off)
```

Only the wake word is a trained model. Every **command** is read from the input-box
text and string-matched (token-based, punctuation-tolerant), so the command word is
stripped before the prompt reaches Claude. Reads are event-driven via macOS
Accessibility (`AXObserver`); dictation start/stop is an `AXPress` on the panel button
(its `AXTitleChanged` is the recording ground truth); other writes are keystrokes. The
turn flows `IDLE → ARMED → DICTATING → ACTING → IDLE`, driven entirely by the
dictation-button state (no silence timer). Details + diagrams in
[ARCHITECTURE](docs/ARCHITECTURE.md).

## Setup

```bash
git clone <this repo> && cd hey-claude
./scripts/setup.sh        # creates .venv, installs deps, generates the LaunchAgent
```

Then grant permissions (one-time, **required** — the daemon is a new process):

1. **System Settings → Privacy & Security → Accessibility** → add your `.venv/bin/python`
   (keystrokes + reading the box).
2. **System Settings → Privacy & Security → Microphone** → add it too (wake listening).

hey-claude forces `AXManualAccessibility` on VS Code at startup so the input box becomes
readable — no VoiceOver needed.

### Train a custom "hey claude" wake word

Until you train one, the daemon runs with a pretrained fallback phrase
(`wake.pretrained_fallback`, default `hey_jarvis`) so you can try it immediately — you
say **"hey jarvis"**. To say "hey claude" instead, follow
[`scripts/train-wake-word.md`](scripts/train-wake-word.md) and set `wake.model` in
`config.toml` to the resulting `.onnx`.

## Run

```bash
# Smoke-test the whole loop WITHOUT the wake word (arms once):
python -m hey_claude --once      # then dictate a prompt and say "okay send"

# Debug: print what hey-claude reads from the focused box:
python -m hey_claude --read

# Normal daemon (also auto-starts at login once the LaunchAgent is loaded):
python -m hey_claude
launchctl load ~/Library/LaunchAgents/com.hey-claude.plist
```

## Restart / recovery

The daemon opens the mic once at startup and binds to the current default input. If
that device disappears (Bluetooth headset drops), hey-claude **stops cleanly** rather than
sit there deaf — the `KeepAlive={SuccessfulExit: false}` LaunchAgent leaves it down so
recovery is deliberate. Reconnect the mic, then:

```bash
hey-claude-restart      # kicks the daemon and prints which input device it bound to
```

`hey-claude-restart` ([`scripts/hey-claude-restart.sh`](scripts/hey-claude-restart.sh), aliased in
`~/.zshrc` by setup) restarts via `launchctl` and reports the mic so you can confirm it
grabbed the headset — not a silent HDMI/virtual fallback. Reconnect *before* restarting;
starting with no input device leaves it deaf. A genuine crash still auto-restarts.

## Debug logging

hey-claude streams a human-readable log of what it's doing — wake → arm → dictate →
sent/cancel/timeout — via the five standard levels (DEBUG/INFO/WARNING/ERROR/CRITICAL).
It goes to **stderr** and to a rolling file you can read after a run:

```bash
python -m hey_claude --debug        # verbose DEBUG (every keystroke, box change, AX callback)
HEY_CLAUDE_DEBUG=1 python -m hey_claude  # same, via env (works for the LaunchAgent too)
python -m hey_claude                # INFO: the high-level narrative only

tail -f ~/Library/Logs/hey-claude/hey-claude.log   # watch it live (rotates at ~2 MB × 3)
```

Under the LaunchAgent, stderr is also captured in `~/Library/Logs/hey-claude/daemon.err.log`.
This is separate from the structured JSONL telemetry below (which is for tuning, not watching).

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
  `pip show onnxruntime`. Debug with `python -m hey_claude --debug` → watch
  `~/Library/Logs/hey-claude/hey-claude.log` for the `max_score` heartbeat.
- **Silent mic (peak ~64)** — the process lacks Microphone permission (separate from
  the extension's). Run `python -m hey_claude --mic-check`; grant Microphone to your
  terminal / the venv python. On a **Mac mini there is no built-in mic** — a
  Bluetooth headset is your only input.
- **`overflow=True` / dropped audio** — bursty Bluetooth delivery; the callback+queue
  capture handles it (heartbeat should show `overflows=0`, ~25 chunks/2s).
- **"hey jarvis" stops working after unplugging the headset** — by design: a mic
  disconnect stops the daemon (look for `mic input lost` in `daemon.err.log`). Reconnect
  and run `hey-claude-restart` (see [Restart / recovery](#restart--recovery)).
- **Command word ends up in the sent prompt** — dictation writes "Okay. Send." with
  punctuation; the token-based matcher strips it. If a new command word misfires,
  check `box_pre_strip` in the telemetry.

## Layout

```
hey_claude/      config ports commands state actions telemetry bootstrap ax keys system wake __main__
scripts/    stats.py  setup.sh  train-wake-word.md  hey-claude-restart.sh
tests/      unit suite + integration/
docs/       REQUIREMENTS · ARCHITECTURE · BUILD-PLAN · HARDENING-PLAN · BACKLOG
```
