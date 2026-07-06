"""FR-6 state machine — pure control logic over injected ports.

States: IDLE → ARMED → DICTATING → (dispatch) → IDLE. All state mutations happen
here; the runtime (__main__) only feeds events: on_wake (from the wake thread via
the run-loop), on_box_change (from the AXObserver), and tick (from a timer).

Unit-tested with fakes + a fake clock (tests/test_state.py).
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Callable, Optional

from .bootstrap import BootstrapResult
from .commands import Fixups


class S(Enum):
    IDLE = "idle"
    ARMED = "armed"
    DICTATING = "dictating"


class StateMachine:
    def __init__(
        self, cfg, bootstrap, keys, ax, commands, telemetry,
        monotonic: Callable[[], float] = time.monotonic,
        beep: Callable[[str], None] = lambda kind: None,
        fixups: Optional[Fixups] = None,
    ):
        self.cfg = cfg
        self.bootstrap = bootstrap
        self.keys = keys
        self.ax = ax
        self.commands = commands
        self.fixups = fixups or Fixups()
        self.tel = telemetry
        self._mono = monotonic
        self._beep = beep

        self.state = S.IDLE
        self._wake_ts: Optional[float] = None
        self._arm_ts: Optional[float] = None
        self._last_activity: Optional[float] = None
        self._boot: Optional[BootstrapResult] = None
        self._pending_wake: Optional[float] = None  # score of the accepted wake, awaiting resolution
        self._last_send: Optional[tuple] = None      # (turn_id, monotonic_ts)

    # ---- events ----------------------------------------------------------
    def on_wake(self, score: float) -> None:
        thr = self.cfg.wake_threshold
        accepted = score >= thr
        if self.state is not S.IDLE:
            # self-trigger guard (FR-1): ignore wake while armed/dictating.
            self.tel.log_wake(score, thr, accepted, followed_through=False, note="ignored_active")
            return
        if not accepted:
            self.tel.log_wake(score, thr, False, followed_through=False)
            return

        # accepted + idle → arm
        self._pending_wake = score
        self.state = S.ARMED
        self._wake_ts = self._arm_ts = self._mono()
        self._beep("armed")

        self._boot = self.bootstrap.ensure_ready()
        if not self._boot.ok:
            self._beep("error")
            self._resolve_wake(False)
            self._log_turn("error", None, None, None, 0)
            self.state = S.IDLE
            return

        self.keys.cmd_esc()   # focus Claude input
        self.keys.cmd_d()     # start dictation
        self._last_activity = self._mono()
        self.state = S.DICTATING
        self.ax.start_observing(self.on_box_change)

    def on_box_change(self, text: str) -> None:
        if self.state is not S.DICTATING:
            return
        self._last_activity = self._mono()
        m = self.commands.match(text)
        if m is not None:
            self._dispatch(m, text)

    def tick(self) -> None:
        now = self._mono()
        if self.state is S.DICTATING and self._last_activity is not None:
            if now - self._last_activity > self.cfg.disarm_timeout_s:
                self.ax.stop_observing()
                self.keys.cmd_d()  # stop dictation
                self._beep("error")
                self._resolve_wake(False)
                self._log_turn("timeout", None, None, None, 0)
                self.state = S.IDLE
        elif self.state is S.ARMED and self._arm_ts is not None:
            if now - self._arm_ts > self.cfg.arm_timeout_s:
                self._beep("error")
                self._resolve_wake(False)
                self.state = S.IDLE

    # ---- internals -------------------------------------------------------
    def _dispatch(self, m, text: str) -> None:
        self.ax.stop_observing()
        self.keys.cmd_d()  # stop dictation → frees the mic

        box_post = m.post_text
        if m.action == "send":
            if not m.post_text.strip():
                # box was only the command → nothing to send; treat as a no-op abort.
                self._beep("error")
                self._resolve_wake(True)
                self._log_turn("empty", m.action, m.prefix, text, 0, box_post=m.post_text)
                self.state = S.IDLE
                return
            corrected = self.fixups.apply(m.post_text)
            if corrected != m.post_text:
                # a mishearing was corrected → rewrite the whole box (read→rewrite path):
                # select-all + delete, retype the fixed prompt, then Return.
                self.keys.clear()
                self.keys.type_text(corrected)
                self.keys.ret()
                box_post = corrected
            else:
                self.keys.backspace(m.strip_len)
                self.keys.ret()
            outcome, strip = "sent", m.strip_len
        elif m.action == "cancel":
            self.keys.clear()
            outcome, strip = "cancelled", 0
        else:  # stop
            self.keys.esc()
            outcome, strip = "stopped", 0

        self._beep(m.action)
        self._resolve_wake(True)
        turn_id = self._log_turn(outcome, m.action, m.prefix, text, strip, box_post=box_post)

        if outcome in ("cancelled", "stopped") and self._last_send is not None:
            tid, ts = self._last_send
            dt = (self._mono() - ts) * 1000.0
            if dt <= self.cfg.correction_window_ms:
                self.tel.log_correction(tid, m.action, dt)
        if outcome == "sent":
            self._last_send = (turn_id, self._mono())
        self.state = S.IDLE

    def _resolve_wake(self, followed_through: bool) -> None:
        if self._pending_wake is not None:
            self.tel.log_wake(self._pending_wake, self.cfg.wake_threshold, True, followed_through)
            self._pending_wake = None

    def _log_turn(self, outcome, command, prefix, box_pre, strip, box_post=None) -> str:
        latency = int((self._mono() - self._wake_ts) * 1000) if self._wake_ts else None
        b = self._boot
        return self.tel.log_turn(
            outcome=outcome, command=command, prefix=prefix,
            box_pre=box_pre, box_post=box_post, strip_chars=strip, latency_ms=latency,
            warm=(not b.cold_start) if b else True,
            cold_start=b.cold_start if b else False,
            bootstrap_ms=b.ms if b else None,
            focus_gate=b.focus_gate if b else None,
        )
