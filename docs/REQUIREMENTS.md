# hey-claude — Requirements (draft v0)

A wake-word voice controller for driving Claude Code hands-free on macOS. Listens
only after a wake word, recognizes a small fixed command vocabulary, and injects
the corresponding keystrokes into the focused window (VS Code extension or terminal).

Status: **drafting** — nothing built yet. This doc is the source of truth for scope.

---

## 1. Problem / Goal

Work with the Claude Code VS Code extension without sitting at the keyboard:
start a dictation turn and submit it hands-free, and approve/deny the common
prompts, using voice from across the room.

Success = "Hey Claude" → speak a prompt → "send", with no keyboard contact for a
normal turn, and a spoken "approve" for the routine permission prompts.

## 2. Primary user story

> As the sole user on my own Mac, I want to trigger Claude Code's dictation and
> submit/approve by voice, so I can iterate from the couch without a keyboard.

Single-user, personal machine. No multi-user, no accounts, no distribution (yet).

## 3. Functional requirements

### FR-1 Wake word
- Idle state listens continuously but takes **no action** until the wake phrase.
- Wake phrase (working default): **"hey claude"** — configurable.
- On wake: audible/visible cue (beep or menu-bar state change) so I know it's armed.

### FR-2 Command vocabulary (fixed, tiny)
After wake (or always-on per command — TBD, see Q3), recognize:
| Command | Action (keystroke) | Purpose |
|---|---|---|
| `start`   | Cmd+D              | Begin Claude Code voice dictation (`/voice`) |
| `send`    | Return             | Submit the current input |
| `approve` | types `yes` + Return | Accept a permission prompt |
| `deny`    | types `no` + Return  | Reject a permission prompt |
| `stop`    | Esc                | Cancel / interrupt |

Vocabulary must be easy to extend in config.

### FR-3 Action layer
- Inject keystrokes into whichever window is focused (chosen: **keystrokes**, not
  tmux piping). Must work for both the VS Code extension and a terminal session.
- Mechanism candidate: `cliclick` or macOS Accessibility (AXUIElement) key events.

### FR-4 Feedback
- Clear indication of state: idle / armed / command-recognized.
- Minimal: menu-bar icon or terminal log line + a short beep on wake and on action.

### FR-5 Config
- Single config file (wake phrase, command→keystroke map, mic device, thresholds).

## 4. Non-functional requirements

- **NFR-1 Offline / private:** no audio leaves the machine. (Rules out cloud ASR.)
- **NFR-2 Low idle cost:** wake-word listening must be light on CPU/battery.
- **NFR-3 Low false-trigger rate:** normal conversation must not fire commands.
- **NFR-4 Fast:** wake→action latency target < ~1s.
- **NFR-5 macOS-native:** Apple Silicon, current macOS. No other OS in scope.
- **NFR-6 Simple install:** documented setup; ideally no paid/keyed services.

## 5. Chosen / leaning decisions

- Action layer = **keystrokes** (decided).
- Location = `~/Documents/GitHub/hey-claude`, local git (decided).
- Approach leaning: **each command is its own wake word** (via openWakeWord), so
  there's no general ASR at all — most robust for a tiny vocab, fully offline,
  no signup. (See Q1.)

## 6. Candidate building blocks (from GitHub survey)

- Wake word: **dscripka/openWakeWord** (2.5k⭐, OSS, no signup) or
  **Picovoice/porcupine** (4.9k⭐, free tier, needs key).
- Full offline assistant framework (heavier): **rhasspy/rhasspy** (wake + intent).
- Keystroke injection: `cliclick`.
- No existing repo does the full "wake → command → Mac keystroke → Claude Code"
  chain; that glue (~80 lines) is what we build.

## 7. Out of scope (for now)

- Non-macOS platforms.
- Multi-user / distribution / packaging as a product.
- Free-form dictation of the *prompt itself* — Claude Code's own `/voice` does that.
- Controlling apps other than Claude Code.
- Cloud ASR / LLM-based intent parsing.

## 8. Open questions

- **Q1 Engine:** openWakeWord (no signup, train phrases) vs Porcupine (easier,
  needs free key) vs Rhasspy (batteries-included, heavier)?
- **Q2 Command recognition:** train each command as its own wake word (no ASR), or
  wake word + a small ASR/intent step for the commands?
- **Q3 Activation model:** strict wake-then-listen window, or always-listening for
  each command phrase (no separate wake word)?
- **Q4 Language:** Python (fastest given the libs) confirmed?
- **Q5 Feedback surface:** menu-bar app vs headless terminal daemon for v1?
- **Q6 Keystroke tool:** `cliclick` (external dep) vs native Accessibility API?

## 9. Acceptance (v1 "done")

- [ ] Saying the wake phrase arms the tool with a clear cue.
- [ ] Each command in FR-2 reliably fires its keystroke into the focused window.
- [ ] Normal conversation does not false-trigger over a 10-min session.
- [ ] Runs fully offline.
- [ ] One-command start; config file documented in README.
