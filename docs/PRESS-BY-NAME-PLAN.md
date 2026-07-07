# Press-by-name via AXPress — plan

Backlog item: *"find an element whose label contains a spoken keyword and `AXPress`
it; `AXPress` a named button."* Enables answering Claude Code choice/permission
dialogs and hitting panel buttons entirely by voice.

## Decisions (2026-07-07)

- **Phrasing:** `<prefix> press <label>` — the same `okay`/`ok` prefix that gates
  `send`/`cancel`/`stop`, so a normal prompt containing "press" can't hijack. The
  label is everything spoken after "press".
- **Scope:** buttons **and** dialog options — search `AXButton` + `AXRadioButton`.
  A question dialog can be answered end-to-end ("okay press yes" → radio; "okay
  press submit" → the *Submit answers* button).

## Why it drops into the existing pipeline

The turn's "focus input" keystroke is **Cmd+Esc** (a Claude-Code focus shortcut),
*not* plain Esc — so arming a turn does **not** dismiss an open dialog. So press
reuses the wake→dictate→observe-box loop: the phrase is dictated into the box,
`on_box_change` matches it as a `press` command, and instead of sending we
`AXPress` the matching control and clear the box.

## Changes

- **`ax.py`** — extract the BFS walker as `_bfs_find(root, predicate)` (the exact
  walker `_find_element` already uses; `_find_element` becomes a thin wrapper, no
  behavior change). Add `press_by_name(keyword, roles)`: BFS for the first node
  whose role ∈ roles and whose `AXTitle`/`AXDescription`/`AXValue` *contains* the
  keyword (case-insensitive), then `AXPress` it. Retry 6× (the webview AX tree is
  briefly stale after a dialog renders — mirrors `_dictation_button`).
- **`commands.py`** — `Match` gains `target`. New `Commands.match_press(text)`:
  find the last `press` verb preceded by a prefix; target = normalized tokens after
  it. Token-based like `match` (survives "Okay. Press. Submit.").
- **`actions.py`** — `perform` handles `action == "press"` via `_press`: free the
  mic first, wait out the post-dictation re-commit (`read_box_settled`), `clear`
  the dictated command text so it never lingers as an unsent prompt, then
  `press_by_name`. Outcome `pressed`/`not_found`; beep `send`/`error` (no new sound).
- **`state.py`** — `on_box_change` tries `match` then `match_press`. `_act` is
  already action-agnostic.
- **`config.py` / `config.toml`** — `[commands] press_verbs` (default `["press"]`)
  and `press_roles` (default `["AXButton","AXRadioButton"]`).
- **`ports.py`** — add `press_by_name` to `AXPort`; **`tests/fakes.py`** scriptable
  `press_found`.

## Out of scope (follow-ups)

- **Background-tab dialogs.** Only the ACTIVE tab is in the AX tree — if the target
  isn't found we fail with the error beep + a log line; we do **not** auto-switch
  tabs. (Backlog: focus the tab first.)
- **Keystroke fallback** for widgets that don't expose `AXPress` (↑/↓ + Enter, Esc)
  — separate backlog line.
