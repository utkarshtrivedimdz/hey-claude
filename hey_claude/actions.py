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
    def __init__(self, keys, ax, fixups: Optional[Fixups] = None, press_roles=None):
        self.keys = keys
        self.ax = ax
        self.fixups = fixups or Fixups()
        self.press_roles = tuple(press_roles) if press_roles else ("AXButton", "AXRadioButton")

    def _trace(self, label: str) -> None:
        """DISPATCH-TRACE: log the live box so we can see each keystroke's effect."""
        try:
            box = self.ax.read_box()
        except Exception as e:  # never let tracing break a turn
            box = f"<read_box error: {e!r}>"
        log.info("DISPATCH-TRACE %s | box=%r", label, box)

    def perform(self, m, text: str) -> ActionOutcome:
        """Execute matched command `m` while dictation is still LIVE, then free the mic.

        Critical: the dictated text only exists in the box while the mic is on — it's the
        dictation session's interim buffer, not committed AXValue. Stopping the mic first
        empties the box (the text only reappears when dictation restarts), so keystrokes
        would hit nothing. So we strip/submit/clear on the live text FIRST, then turn the
        mic off afterward.
        """
        log.info("DISPATCH-TRACE perform START action=%s strip_len=%s post_text=%r match_text=%r",
                 m.action, m.strip_len, m.post_text, text)
        self.ax.stop_observing_box()  # our own keystrokes must not re-trigger command match
        self._trace("after stop_observing_box (dictation still live)")

        if m.action == "press":
            # press owns its own mic-off ordering (frees the mic BEFORE clearing the box),
            # so it returns directly rather than falling through to the trailing mic-off.
            return self._press(m)

        if m.action == "send":
            out = self._send(m)
        elif m.action == "cancel":
            self.keys.clear()
            self._trace("after cancel clear")
            out = ActionOutcome("cancelled", "cancel", strip=0, box_post=m.post_text)
        else:  # stop
            self.keys.esc()
            self._trace("after stop esc")
            out = ActionOutcome("stopped", "stop", strip=0, box_post=m.post_text)

        # Submitted / cleared on the live text — now free the mic. (Return usually ends
        # dictation on its own, so this is typically a no-op guard.)
        if self.ax.dictation_on():
            self.ax.press_dictation()
            self._trace("after mic off")
        return out

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
            self._trace("after fixup clear+type")
            self.keys.ret()
            self._trace("after fixup Return")
            return ActionOutcome("sent", "send", strip=m.strip_len, box_post=corrected)

        log.info("DISPATCH-TRACE send fast path: backspace(%d) then Return", m.strip_len)
        self.keys.backspace(m.strip_len)
        self._trace(f"after backspace({m.strip_len})")
        self.keys.ret()
        self._trace("after Return (empty/placeholder ⇒ sent)")
        return ActionOutcome("sent", "send", strip=m.strip_len, box_post=m.post_text)

    def _press(self, m) -> ActionOutcome:
        """AXPress the control whose label contains `m.target`; clear the dictated command.

        Unlike send, press does NOT submit the box — the phrase "okay press submit" is
        junk we must not leave behind. The interim dictation text re-commits into the box
        after mic-off, so we free the mic first, wait out that re-commit, then clear the
        box, and finally AXPress the target (an action on the element ref, focus-independent).
        """
        target = (m.target or "").strip()
        if not target:
            log.warning("'press' matched but no target label — no-op abort")
            return ActionOutcome("no_target", "error", strip=m.strip_len, box_post="")

        if self.ax.dictation_on():
            self.ax.press_dictation()
            self._trace("after mic off (press)")
        self.ax.read_box_settled()   # wait out the post-dictation re-commit before clearing
        self.keys.clear()            # remove the dictated command text so it never lingers
        self._trace("after clear (press)")

        ok = self.ax.press_by_name(target, self.press_roles)
        self._trace(f"after AXPress {target!r} ok={ok}")
        if ok:
            return ActionOutcome("pressed", "send", strip=m.strip_len, box_post="")
        log.warning("press: no control matched %r — nothing pressed", target)
        return ActionOutcome("not_found", "error", strip=m.strip_len, box_post="")
