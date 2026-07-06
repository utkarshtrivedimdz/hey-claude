# hey-claude — Requirements (draft v0)

A wake-word voice controller for driving Claude Code hands-free on macOS. Listens
only after a wake word, recognizes a small fixed command vocabulary, and injects
the corresponding keystrokes into the **Claude Code VS Code extension** (the sole
target; terminal is out of scope — see §7).

Status: **spec-complete, decisions locked, unbuilt** — architecture verified
end-to-end (control surface, box-reading, event-driven observe, keystroke I/O all
tested 2026-07-06). Engine=openWakeWord, surface=headless daemon, commands=Option A
(dictate+strip), lang=Python+pyobjc. See `BUILD-PLAN.md` for the build path.

---

## 1. Problem / Goal

Work with the Claude Code VS Code extension without sitting at the keyboard:
start a dictation turn and submit it hands-free, and approve/deny the common
prompts, using voice from across the room.

Success = "Chotu" → speak a prompt → "send", with no keyboard contact for a
normal turn, and a spoken "approve" for the routine permission prompts.

## 2. Primary user story

> As the sole user on my own Mac, I want to trigger Claude Code's dictation and
> submit/approve by voice, so I can iterate from the couch without a keyboard.

Single-user, personal machine. No multi-user, no accounts, no distribution (yet).

## 3. Functional requirements

### FR-0 Primary "chotu" happy path
The end-to-end flow saying **"chotu"** should drive (no keyboard):

1. **Bootstrap (chotu-owned, idempotent). `code` CLI is NOT on PATH — use `open`:**
   - **VS Code running?** if not, `open -n -a "Visual Studio Code"`. Detect via
     `osascript -e 'application "Visual Studio Code" is running'` (bundle id
     `com.microsoft.VSCode`).
   - **geofast workspace active?** the workspace is
     `/Volumes/GeofastStorage/GitHub/geofast.code-workspace` (multi-root). If not
     active, `open "<that file>"`. **Detect (Q8, solved):** read the frontmost VS
     Code window's `AXTitle` — it contains `geofast (Workspace)` when active.
   - **Focus the Claude input:** emit **Cmd+Esc** — verified to focus the Claude
     input from an unfocused window. Always do this before Cmd+D: it guarantees
     focus is in the Claude input, so Cmd+D drives dictation and never falls
     through to VS Code's native "select next occurrence" (Cmd+D). **Ordering is
     load-bearing: Cmd+Esc → Cmd+D.**
2. **Start dictation:** emit **Cmd+D** — the VS Code extension's record shortcut
   ("tap or hold to record", verified in-UI) — so the extension's own dictation
   transcribes the prompt into the input box ("fully form the sentence"; visible
   on screen). chotu does *not* run its own speech-to-text.
3. **Detect the command from the box (Option A):** while dictation runs, chotu
   watches the box **event-driven** — an `AXObserver` on `kAXValueChangedNotification`
   fires on each transcription update (polling `AXValue` is the fallback; Q12). The
   **trailing command word** is
   both the "done" signal *and* the action — e.g. you say "…retry loop. **Okay
   send.**". Disambiguated by an `okay`/`chotu` **prefix** or a brief pause (Q11b)
   so a prompt that legitimately ends in "send" doesn't fire. On match, chotu taps
   **Cmd+D to stop dictation**. (Silence timeout still runs as a disarm safety.)
4. **Strip + act:** the command word was transcribed *into* the box, so chotu first
   **backspaces the trailing command phrase out** (cursor is already at end; AX
   writes don't persist per Q11a) — it must never reach Claude — then:
   - **send** (`okay/ok/sure/send/yes/yeah/go/confirm`) → **Return**
   - **cancel** (`no/cancel/scratch that`) → **Cmd+A + Delete** (clear), stay armed
   - **stop** (`stop/interrupt`) → **Esc**
   A short ack beep fires before the action. (Confirm + command are fused into the
   one utterance — no separate "okay to send?" prompt.)

**Layer ownership:** bootstrap + confirm-gate = **chotu** (local controller);
transcription = the **VS Code extension's built-in dictation**; **command
recognition = reading the input box `AXValue` and string-matching** (Q2/Q10 —
verified: box is an `AXTextArea`, readable once chotu forces
`AXManualAccessibility=true` at startup); the Claude *model/agent* is not in this
control loop. Only "chotu" itself needs a trained wake-word model. See Q7 for how
"sentence done" is detected.

**Verified extension control surface (2026-07-06):** the extension exposes the mic
as a UI button + the **Cmd+D** keybinding, and **Cmd+Esc** to focus/unfocus — but
**no command ID or `vscode://` URI to start recording or submit programmatically**.
So chotu's only ways to "press record" are (a) emit the Cmd+D keystroke, or (b)
AXPress the mic button via macOS Accessibility. Emitting Cmd+D from the wake-word
script *is* the "activate via script" model — the keystroke stays swappable in
config. (The CLI's `/voice` uses Space/`voice:pushToTalk` instead — not applicable
to the extension.)

### FR-1 Wake word
- Idle state listens continuously but takes **no action** until the wake phrase.
- Wake phrase (working default): **"chotu"** (छोटू, "little helper") — configurable.
- On wake: audible/visible cue (beep or menu-bar state change) so I know it's armed.
- **Self-trigger guard:** the wake word is **ignored while dictating/confirming**
  (see FR-6), so saying "chotu" mid-flow — or "chotu" landing in the dictated
  prompt — does not re-arm or recurse.

### FR-2 Command vocabulary
Commands are **read from the input box `AXValue` and string-matched** (Q2/Q10) —
not separate wake words. Only "chotu" is a trained model; "start dictation" is
folded into the wake flow (FR-0), so there is **no standalone `start` command**.

| Command | Action | Trigger (string-match on box, or state) |
|---|---|---|
| `send`    | Return               | yes-word at end of box: okay/ok/sure/send/yes/yeah/go/confirm |
| `cancel`  | Cmd+A + Delete (clear); stay armed | no-word: no/cancel/scratch that |
| `stop`    | Esc                  | `stop` / `interrupt` — cancels/interrupts a running turn |
| `approve` | types `yes` + Return | only in a permission-prompt state; **moot under "Bypass permissions"** |
| `deny`    | types `no` + Return  | only in a permission-prompt state; **moot under "Bypass permissions"** |

- **`stop` is overloaded** (global Esc *and* a cancel no-word) and is a common
  English word → false-trigger risk (NFR-3). Keep it a *distinct* utterance and/or
  require the confirm state; revisit if it mis-fires.
- Command words/synonyms live in config (FR-5) and expand freely — string-matching,
  no retraining.

### FR-3 Action layer
- Inject keystrokes into the **Claude Code VS Code extension** (chosen:
  **keystrokes**, not tmux piping). Extension only — terminal Claude Code uses a
  different dictation binding (Space/`voice:pushToTalk`, not Cmd+D) and is out of
  scope for v1.
- Mechanism: **`osascript` System Events `key code`** (no install; Accessibility
  granted). `cliclick` is a fallback if osascript timing proves flaky.
- **Focus-safety gate (required):** keystrokes go to whatever is frontmost, so
  **never inject unless** the frontmost app is VS Code (`com.microsoft.VSCode`)
  **and** the window `AXTitle` contains `geofast (Workspace)`. If not, bootstrap
  **raises VS Code to frontmost** first; if it still can't verify, **abort with an
  error cue** rather than firing keystrokes into the wrong app.
- Box edits (strip a command word, clear on cancel) are **keystroke-based**
  (Cmd+A + retype / backspace) — AX `set` doesn't persist (Q11a).

### FR-4 Feedback
- Clear indication of state: idle / armed / dictating / confirming / acted (FR-6).
- Minimal: menu-bar icon or terminal log line + a short beep on wake and on action.
- **Persistent event log** for tuning — full spec in **FR-7**. Local only (NFR-1).

### FR-5 Config
Single config file. Keys:
- `wake_phrase` (default `chotu`), `mic_device`, detection `threshold`s.
- `command_words`: map of action → synonym list (send/cancel/stop/approve/deny) —
  string-matched against the box; extend freely, no retraining.
- `command_prefix`: optional required prefix before a command word (`okay`/`chotu`)
  for disambiguation (Q11b, Option A). Empty = match a bare trailing command word.
- `disarm_timeout_ms`: silence-while-dictating → disarm to idle (safety; never sends).
- `target`: app bundle id + workspace path + expected window-title substring
  (defaults: `com.microsoft.VSCode`, `…/geofast.code-workspace`, `geofast
  (Workspace)`).
- `keymap`: action → key code (swappable, per FR-0 verified control surface).
- `telemetry`: `enabled`, `retention_days`, `store_prompt_text`
  (`full`/`hash`/`length_only`/`off`) — FR-7.

### FR-6 State machine (control layer)
Explicit states with timeout + error transitions (the happy path is FR-0):
- **idle** → (wake word) → **armed**  [emit wake cue]
- **armed** → bootstrap + focus-safety gate (FR-3); on failure → error cue → idle.
  Arm auto-expires to **idle** after a config timeout if nothing follows.
- **armed** → **dictating** (Cmd+D). Wake word ignored here (FR-1 self-trigger).
- **dictating** → chotu **observes the box** (`AXObserver` on value-changed,
  event-driven; polling fallback — Q12); a **trailing command word** (disambiguated,
  Q11b) is the done+action signal → stop dictation (Cmd+D) → **acting**. (Option A:
  command is read from the box, not heard separately.)
- **acting** → **strip** the trailing command phrase from the box (FR-0 step 4;
  keystrokes, not AX write) → execute send/cancel/stop → **idle**.
- **Silence timeout while dictating** → disarm to **idle** with a cue; **never
  auto-sends** (cancel-safe default).
- Any state → (`stop`/interrupt) → Esc → **idle**.
- Every non-idle state has a timeout back to idle so the tool can't get stuck armed.

### FR-7 Telemetry / tuning data (so it gets battle-tested)
The heuristics that can't be settled by reasoning — wake threshold (NFR-3),
disambiguation prefix-vs-pause (Q11b), A-vs-B, sentence-done mode (Q7), timeout
durations — get settled by **data from real sessions**. Capture it locally.
**Concrete record schema, redaction, retention, and metric definitions live in
[`ARCHITECTURE.md` §6](ARCHITECTURE.md#6-telemetry--tuning-detailed).**

- **Format:** append-only **JSONL**, **local only** (NFR-1), rotating retention
  (`retention_days`). Every event stamped with ts + warm/cold + state.
- **Wake events:** confidence score, accepted/rejected, and whether it led to a
  real turn (→ false-accept / false-reject rates for threshold tuning).
- **Command events:** matched command, whether `command_prefix` was present, box
  text **pre-strip**, resulting **post-strip** text, and which sentence-done
  trigger fired.
- **Outcome + latency:** sent / cancelled / stopped / timed-out; wake→action ms
  (warm vs cold, per NFR-4).
- **Errors:** bootstrap failures, focus-gate aborts, observer stale/re-register,
  placeholder-as-empty hits.
- **Implicit feedback (the key signal):** a `stop`/`cancel`/`scratch` **shortly
  after** an action flags that action as a **likely mis-fire** — the training
  signal for Q11b disambiguation and threshold tuning.
- **Privacy knob:** `store_prompt_text` = `full` | `hash` | `length_only` | `off`
  (personal machine, single user — but redaction available). No audio ever stored.
- **Derived metrics** (a small `stats` script): false-trigger rate, command
  precision (via implicit mis-fires), p50/p95 latency, disarm/timeout rate.

## 4. Non-functional requirements

- **NFR-1 Offline / private:** no audio leaves the machine. (Rules out cloud ASR.)
- **NFR-2 Low idle cost:** wake-word listening must be light on CPU/battery.
- **NFR-3 Low false-trigger rate:** normal conversation must not fire commands.
- **NFR-4 Fast:** **warm** wake→action latency target < ~1s. **Cold** start
  (launching VS Code) is exempt — it's inherently multi-second; give a "starting"
  cue and proceed when ready.
- **NFR-5 macOS-native:** Apple Silicon, current macOS. No other OS in scope.
- **NFR-6 Simple install:** documented setup; ideally no paid/keyed services.
- **NFR-7 Permissions (TCC):** chotu is a **new process** and needs its own
  **Microphone** (wake-word listening) and **Accessibility** (keystrokes + reading
  the box) grants on first run — and it must set `AXManualAccessibility=true` on VS
  Code at startup (Q10). Document these grants; they're the main install friction.
- **NFR-8 Lifecycle:** runs as a **launchd LaunchAgent** — starts at login, restarts
  on crash, so it's always ready from the couch (not a manual foreground process).

## 5. Chosen / leaning decisions

- Action layer = **keystrokes** (decided).
- Location = `~/Documents/GitHub/hey-claude`, local git (decided).
- Recognition = **one trained wake word ("chotu") + read-the-box string-matching
  for all commands** (decided; Q2/Q10). No per-command wake words, no general ASR.
  Fully offline, no signup. (Supersedes the earlier "each command is its own wake
  word" leaning, which became unnecessary once box-reading was proven.)

## 6. Candidate building blocks (from GitHub survey)

- Wake word: **dscripka/openWakeWord** (2.5k⭐, OSS, no signup) or
  **Picovoice/porcupine** (4.9k⭐, free tier, needs key).
- Full offline assistant framework (heavier): **rhasspy/rhasspy** (wake + intent).
- Keystroke injection: `cliclick` (**not currently installed** — `brew install
  cliclick`), OR **`osascript` System Events** `key code` (no install; Accessibility
  is already granted). e.g. Cmd+D = `key code 2 using command down`, Cmd+Esc =
  `key code 53 …`, Return = `key code 36`.
- App control (verified): `code` CLI is **not on PATH** — use `open -n -a "Visual
  Studio Code"` and `open "<geofast.code-workspace>"`. Workspace = active when the
  VS Code window `AXTitle` contains `geofast (Workspace)`.
- AX read/observe layer: **pyobjc** (`pyobjc-framework-ApplicationServices`) — needed
  for the event-driven `AXObserver` (Q12) and robust `AXValue` reads (avoids
  osascript path-walking staleness). *Not installed yet.* Keystrokes can stay
  `osascript`, or move to pyobjc `CGEvent` to keep one dependency.
- **MCP is not applicable** to this control path: MCP is a tool interface an
  LLM/agent consumes, not an IPC an external keystroke script drives; it exposes no
  hook to start the extension's dictation, submit, or inject into a running session
  (those hooks don't exist regardless). See §Verified control surface under FR-0.
- No existing repo does the full "wake → command → Mac keystroke → Claude Code"
  chain; that glue (~80 lines) is what we build.

## 7. Out of scope (for now)

- Non-macOS platforms.
- Multi-user / distribution / packaging as a product.
- Free-form dictation of the *prompt itself* — the VS Code extension's own
  dictation (Cmd+D) does that; chotu only starts/stops it and reads the result.
- Controlling apps other than Claude Code.
- **Terminal Claude Code** — extension only for v1 (terminal dictation uses a
  different binding; would need its own keymap and focus/detection logic).
- Cloud ASR / LLM-based intent parsing.

## 8. Open questions

- **Q1 Engine:** ~~DECIDED — openWakeWord~~ (offline, no key, coexists with
  dictation; train "chotu" via synthetic-data pipeline). `SFSpeechRecognizer` kept
  as the Apple-native fallback (see below).
  - Note: **"chotu"** is a custom phrase — not in openWakeWord's pre-built set, so
    we must generate a model for it via its synthetic-data training pipeline
    (piper TTS → augment → train). A 2-syllable word trains cleanly; budget one
    pass to tune the detection threshold against false triggers. On Porcupine,
    a custom keyword needs the (free) Picovoice console + key.
  - **Apple-native options evaluated (mostly worse fit for always-on + dictation):**
    - *Siri / "Hey Siri" wake:* can't set a custom wake word or intercept Siri; only
      "Hey Siri → run Shortcut 'chotu'" — two-part prefix, **cloud** (breaks NFR-1),
      chime + latency (breaks NFR-4). No good for the `send`/`cancel` loop.
    - *macOS Voice Control:* on-device, custom commands, **zero training** — but
      **always-listening and parses all speech as commands → conflicts with the
      extension's dictation** and takes over the UI. Architecturally incompatible.
    - *`SFSpeechRecognizer` (on-device):* the one viable Apple path — continuous
      on-device ASR, **no wake model to train**; string-match "chotu"/commands
      ourselves. Cost: heavier idle CPU/battery than a purpose-built wake engine
      (NFR-2), and must be paused during dictation to avoid contention.
    - **Verdict:** openWakeWord/Porcupine fire *only* on the phrase → low-power,
      always-on, and **coexist with dictation** (chotu reads the box, doesn't
      recognize). Keep them as primary; `SFSpeechRecognizer` is the Apple-native
      fallback if avoiding a trained model outweighs the idle-cost hit.
- **Q2 Command recognition:** ~~RESOLVED — read commands from the input box~~
  (Q10 proved the box is readable). Architecture: **only "chotu" needs a trained
  wake-word model** (to arm). All commands are **read from the box `AXValue` and
  string-matched** — so the extension's dictation does recognition, natural
  phrasing expands with zero new models, and the FR-0 yes/no synonym lists work as
  plain string matches from day one. No per-command wake-word models, no separate
  ASR. Command words are dictated then **stripped** before send (Option A, Q11b).
- **Q3 Activation model:** ~~mostly RESOLVED by FR-6~~ — **strict wake-then-listen**:
  "chotu" arms, then the state machine drives dictate→confirm with timeouts back to
  idle. Commands are read from the box (not always-on phrases), so the old
  "always-listening per command" option is moot. Only tunable left: the arm/confirm
  timeout durations.
- **Q4 Language:** ~~CONFIRMED — Python~~. pyobjc drives the whole AX layer (read +
  event-driven observe, Q10/Q12) and openWakeWord is Python; keystrokes via
  `osascript`/`CGEvent`. Verified working 2026-07-06.
- **Q5 Feedback surface:** ~~DECIDED — headless daemon for v1~~ (LaunchAgent + beeps
  + FR-7 log; menu-bar app deferred to §10).
- **Q6 Keystroke tool:** ~~`cliclick` vs Accessibility~~ — **leaning `osascript`
  System Events `key code`** (no install; Accessibility already granted). cliclick
  remains an option if osascript timing proves flaky.
- **Q7 "Sentence done" detection:** ~~decided by testing~~ — **build both**
  (silence timeout ~1.5s *and* an explicit end-word), make it a config switch, and
  pick whichever feels natural in use. Not a design blocker.
- **Q8 Workspace identity:** ~~SOLVED~~ — the geofast workspace is
  `/Volumes/GeofastStorage/GitHub/geofast.code-workspace` (multi-root); detect it's
  active by reading the VS Code window `AXTitle` for `geofast (Workspace)`.
- **Q9 Keystroke conflicts:** ~~RESOLVED by testing~~ — **Cmd+Esc** focuses the
  Claude input from an unfocused window; **Cmd+D** drives dictation (tap).
  Resolution: always fire **Cmd+Esc → Cmd+D**, which moots the native-multicursor
  conflict. (Residual, minor: the *fully-closed* panel case — "unfocused" was
  tested, "closed" not explicitly; if Cmd+Esc doesn't reopen a closed view, add a
  one-time "Focus Claude Input" command call. Not a v1 blocker.)
- **Q10 Can chotu read the Claude input box?** ~~RESOLVED — YES~~ (tested
  2026-07-06). Chromium's a11y tree is off by default (window = one opaque
  `AXGroup`), but forcing **`AXManualAccessibility=true`** on the Electron process
  builds it: the input is then an **`AXTextArea` whose `AXValue` is fully
  readable** (probe returned `[hello world]` verbatim). Caveats baked into the
  design: (1) chotu must **set the flag at startup** — it's per-process and resets
  on VS Code relaunch; (2) the webview re-renders constantly so AX refs go stale in
  ms — **read atomically with a retry loop**; (3) read the **focused** element, so
  Cmd+Esc (focus box) must precede the read. This makes box-reading the primary
  command path — **no trained confirm-word models needed** (see Q2).
- **Q11a Is `AXValue` settable?** ~~RESOLVED — NO (in practice)~~ (tested
  2026-07-06). The attribute reports `settable=true`, but writes **don't persist** —
  the box is a React-controlled input that re-renders from its own state and
  ignores AX writes (set to a test string → readback was the empty placeholder).
  **Implication:** chotu edits the box with **keystrokes** (Cmd+A + retype, or
  backspace-to-strip a trailing command word), not AX `set`. **Keystroke
  write+modify verified live** (2026-07-06): typed "abc def" → backspaced 3 →
  AX read confirmed `[abc ]`. So chotu has **full read (AX) + write (keystroke)**
  control of the box — enabling not just command-stripping but composing text:
  **voice snippets/macros** (keyword → type a canned block), dictation edits, or
  cursor inserts. (Scope for v1 TBD — see note below.)
  - **Sub-finding — placeholder-as-empty:** an empty box returns its **placeholder**
    as `AXValue` (e.g. `Queue another message…`, varies by state), not `""`. chotu's
    "box empty? / did dictation produce text?" check must treat known placeholder
    strings as empty (or read `AXPlaceholderValue` separately — verify at build).
- **Q11b Keeping command words out of the prompt** — ~~RESOLVED: Option A~~.
  Dictation is on when you say `send`/`stop`/`cancel`, so they get transcribed into
  the box; chotu **detects the trailing command token and backspaces it out** before
  acting, so it never reaches Claude ("chotu" is usually safe — spoken before
  dictation starts). Disambiguation via `command_prefix` (`okay`/`chotu`) or a
  standalone-final-token-after-pause; `stop`/re-dictate recovers a mis-fire. Confirm
  and command fuse into one utterance ("okay send"); one trained model total.
  - *Fallback B (if A false-fires too much):* dictate only the prompt → pause → stop
    dictation → recognize `send`/`cancel` via 1–2 small trained models (box stays
    pristine, no strip, +2 models).
- **Q12 Event-driven box reads (AXObserver)** — ~~RESOLVED — YES, event-driven~~
  (tested 2026-07-06 with pyobjc). An `AXObserver` on `AXValueChanged` registered on
  the focused `AXTextArea` delivered **33/33 change events** (per keystroke) with the
  full current value each time, and the **held element ref stayed valid throughout** —
  no recreation during editing. So **polling is not needed** (kept only as a defensive
  fallback). Notes for build: (1) the earlier "Invalid index" staleness was an
  **`osascript` path-walking artifact**, *not* the element — a **direct
  `AXUIElementRef` in pyobjc is stable**; (2) **re-register the observer on focus**
  each new turn (a *sent* message re-renders the panel; within-a-turn is stable);
  (3) callback must be wrapped `@objc.callbackFor(AXObserverCreate)`; (4)
  `AXIsProcessTrusted()` was True for the process, but the standalone **LaunchAgent
  daemon needs its own Accessibility grant** (won't inherit) — see NFR-7.

## 9. Acceptance (v1 "done")

- [ ] Saying the wake phrase arms the tool with a clear cue.
- [ ] Full happy path (FR-0) works cold: "chotu" → VS Code + geofast workspace up
      → Claude focused → dictate → confirm → send, no keyboard.
- [ ] chotu reads the dictated text from the box (`AXValue`) and matches the
      configured command words; empty/placeholder box handled correctly.
- [ ] Focus-safety gate holds: with a *non*-VS-Code app frontmost, chotu raises VS
      Code (or aborts with a cue) and never injects into the wrong app.
- [ ] Confirm state never auto-sends on silence; `stop` cancels cleanly.
- [ ] Wake word said mid-dictation (or transcribed into the prompt) does not recurse.
- [ ] Normal conversation does not false-trigger over a 10-min session.
- [ ] Runs fully offline; Mic + Accessibility grants documented.
- [ ] Installs as a LaunchAgent (starts at login); config file documented in README.
- [ ] Every turn logs a structured JSONL event (wake score, command, pre/post-strip,
      outcome, latency); a `stats` pass reports false-trigger rate + p50/p95 latency.
- [ ] `pytest -m unit` is green (command match/strip golden table, state machine,
      telemetry redaction) and runs without a Mac GUI / pyobjc; integration smoke
      tests pass locally against a live VS Code. See ARCHITECTURE §7.

## 10. Future expansion (toward more natural)
Not in v1, but proven feasible by the 2026-07-06 tests — the read (AX) + write
(keystroke) control of the box is the enabler for all of these:

- **Natural-language commands.** Beyond fixed words, match natural phrasing from the
  box text ("okay go ahead and send that") — string/intent matching on `AXValue`,
  no new models (Option A's expansion path; Q2/Q11b). Grow the vocab in config.
- **Voice snippets / macros.** A keyword → chotu **types a canned block** (prompt
  preamble, a file path, boilerplate) into the box via keystrokes (write verified,
  Q11a). Turns chotu into a voice text-expander for common prompts.
- **In-place edits before send.** "scratch the last line", "append …", correct a
  dictation slip — read box, recompute, keystroke the delta.
- **Spoken readback / confirm.** TTS the box back ("send: add a retry loop?") for
  eyes-free use across the room, gating on a yes-word.
- **Menu-bar UI** (if v1 ships headless) — visible idle/armed/dictating state.
- **Terminal Claude Code** support (own keymap + focus/detection; §7).
- **Fallback B for commands** (separate trained `send`/`cancel` models) if Option A's
  strip/disambiguation proves too false-fire-prone (Q11b).
