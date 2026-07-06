"""Action dispatch — the *how* of executing a matched command (HARDENING-PLAN Phase 2).

Extracted out of the state machine: `state.py` sequences (DICTATING → ACTING → IDLE)
and owns telemetry + the correction window; `Actions` owns the keystroke/AX choreography
for each command — stop dictation, then send / cancel / stop, including the fixup
rewrite-vs-backspace decision on send.

Pure over injected `keys`/`ax` ports (ARCHITECTURE §7.1) — fake-tested in
tests/test_actions.py, never imports pyobjc.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .commands import Fixups

log = logging.getLogger(__name__)


@dataclass
class ActionOutcome:
    """What `perform` did — enough for the state machine to log + track corrections.

    `outcome` drives the state machine's correction/last-send bookkeeping;
    `beep` is the outcome sound (kept out of `_transition`, which owns only the
    arm beep). `strip`/`box_post` feed `log_turn`.
    """
    outcome: str    # "sent" | "cancelled" | "stopped" | "empty"
    beep: str       # beep kind to fire at the call site ("send"/"cancel"/"stop"/"error")
    strip: int      # chars backspaced (telemetry strip_chars)
    box_post: str   # box text after the command was executed — what reached Claude


class Actions:
    def __init__(self, keys, ax, fixups: Optional[Fixups] = None):
        self.keys = keys
        self.ax = ax
        self.fixups = fixups or Fixups()

    def perform(self, m, text: str) -> ActionOutcome:
        """Execute matched command `m` (box text `text`). Stops dictation first."""
        self.ax.stop_observing_box()  # our own keystrokes must not re-trigger command match
        if self.ax.dictation_on():
            self.ax.press_dictation()  # stop dictation → frees the mic before we touch the box

        if m.action == "send":
            return self._send(m)
        if m.action == "cancel":
            self.keys.clear()
            return ActionOutcome("cancelled", "cancel", strip=0, box_post=m.post_text)
        # stop
        self.keys.esc()
        return ActionOutcome("stopped", "stop", strip=0, box_post=m.post_text)

    def _send(self, m) -> ActionOutcome:
        if not m.post_text.strip():
            # box was only the command → nothing to send; no-op abort (no keystrokes).
            log.warning("'%s' matched but box empty after strip — no-op abort (nothing sent)",
                        m.action)
            return ActionOutcome("empty", "error", strip=0, box_post=m.post_text)

        corrected = self.fixups.apply(m.post_text)
        if corrected != m.post_text:
            # a mishearing was corrected → rewrite the whole box (read→rewrite path):
            # select-all + delete, retype the fixed prompt, then Return.
            log.info("fixup rewrite before send: %r → %r", m.post_text, corrected)
            self.keys.clear()
            self.keys.type_text(corrected)
            self.keys.ret()
            return ActionOutcome("sent", "send", strip=m.strip_len, box_post=corrected)

        log.debug("send fast path: backspace %d + Return", m.strip_len)
        self.keys.backspace(m.strip_len)
        self.keys.ret()
        return ActionOutcome("sent", "send", strip=m.strip_len, box_post=m.post_text)
