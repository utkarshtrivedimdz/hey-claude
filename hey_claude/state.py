"""FR-6 state machine — pure control logic over injected ports.

States: IDLE → ARMED → DICTATING → ACTING → IDLE. All state mutations happen
here; the runtime (__main__) only feeds events: on_wake (from the wake thread via
the run-loop), on_box_change (from the AXObserver), and tick (from a timer).

This module *sequences* turns and owns telemetry + the correction window; the
keystroke/AX choreography of each command lives in `actions.py` (Phase 2).

Unit-tested with fakes + a fake clock (tests/test_state.py).
"""
from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Callable, Optional

from .actions import Actions
from .bootstrap import BootstrapResult
from .commands import Fixups

log = logging.getLogger(__name__)


class S(Enum):
    IDLE = "idle"
    ARMED = "armed"
    DICTATING = "dictating"
    ACTING = "acting"


# Declared legal transitions — the single source of truth for state changes.
# Every value in `_transition` is asserted against this table (HARDENING-PLAN Phase 1).
# DICTATING → ACTING on a command match; ACTING → IDLE once Actions.perform runs.
# DICTATING → IDLE remains for the silence/arm-timeout backstops (no dispatch).
LEGAL: dict[S, set[S]] = {
    S.IDLE: {S.ARMED},
    S.ARMED: {S.IDLE, S.DICTATING},
    S.DICTATING: {S.IDLE, S.ACTING},
    S.ACTING: {S.IDLE},
}


class IllegalTransition(Exception):
    """Raised (in strict/test builds) when a transition is not in LEGAL."""


class StateMachine:
    def __init__(
        self, cfg, bootstrap, keys, ax, commands, telemetry,
        monotonic: Callable[[], float] = time.monotonic,
        beep: Callable[[str], None] = lambda kind: None,
        fixups: Optional[Fixups] = None,
        strict: bool = False,
    ):
        self.cfg = cfg
        self.bootstrap = bootstrap
        self.keys = keys
        self.ax = ax
        self.commands = commands
        self.actions = Actions(keys, ax, fixups, press_roles=getattr(cfg, "press_roles", None))
        self.tel = telemetry
        self._mono = monotonic
        self._beep = beep
        self._strict = strict  # test builds raise on an illegal transition; prod only logs

        self.state = S.IDLE  # initial state — set directly; every later change goes via _transition
        self._wake_ts: Optional[float] = None
        self._arm_ts: Optional[float] = None
        self._boot: Optional[BootstrapResult] = None
        self._pending_wake: Optional[float] = None  # score of the accepted wake, awaiting resolution
        self._last_send: Optional[tuple] = None      # (turn_id, monotonic_ts)

    # ---- events ----------------------------------------------------------
    def on_wake(self, score: float) -> None:
        thr = self.cfg.wake_threshold
        accepted = score >= thr
        if self.state is not S.IDLE:
            # self-trigger guard (FR-1): ignore wake while armed/dictating.
            log.debug("wake %.3f ignored — state=%s (self-trigger guard)", score, self.state.value)
            self.tel.log_wake(score, thr, accepted, followed_through=False, note="ignored_active")
            return
        if not accepted:
            log.debug("wake %.3f below threshold %.3f — ignored", score, thr)
            self.tel.log_wake(score, thr, False, followed_through=False)
            return

        # accepted + idle → arm
        log.info("wake accepted %.3f ≥ %.3f → arming", score, thr)
        self._pending_wake = score
        self._wake_ts = self._arm_ts = self._mono()
        self._transition(S.ARMED, "wake_accepted")  # centralized "armed" beep fires here

        self._boot = self.bootstrap.ensure_ready()
        log.info("bootstrap: ok=%s cold=%s focus_gate=%s %dms",
                 self._boot.ok, self._boot.cold_start, self._boot.focus_gate, self._boot.ms)
        if not self._boot.ok:
            log.error("bootstrap failed (focus_gate=%s) — aborting turn, no keystrokes sent",
                      self._boot.focus_gate)
            self._beep("error")
            self._resolve_wake(False)
            self._log_turn("error", None, None, None, 0)
            self._transition(S.IDLE, "bootstrap_failed")
            return

        self.keys.cmd_esc()   # focus Claude input
        # The Voice-dictation button is the ground truth for the DICTATING state.
        state = self.ax.dictation_on()
        if state is None:
            log.error("Voice-dictation button not found — aborting turn, no dictation")
            self._beep("error")
            self._resolve_wake(False)
            self._log_turn("error", None, None, None, 0)
            self._transition(S.IDLE, "dictation_button_missing")
            return
        self.ax.observe_dictation(self.on_dictation_change)  # watch the on/off (blue) event
        if state:
            self._enter_dictating()          # already recording → we are dictating now
        else:
            self.ax.press_dictation()        # request ON; AXTitleChanged→on drives DICTATING
            log.info("dictation requested (AXPress) — awaiting button-on event")

    def on_dictation_change(self, is_on: bool) -> None:
        """Ground-truth dictation state from the button's AXTitleChanged observer."""
        if self.state is S.ARMED and is_on:
            self._enter_dictating()
        elif self.state is S.DICTATING and not is_on:
            # user clicked the mic off (or macOS dropped it) → end the turn, never auto-send.
            log.info("dictation button OFF externally — disarming (never auto-sends)")
            self.ax.stop_observing_box()
            self.ax.stop_observing_dictation()
            self._beep("dropped")
            self._resolve_wake(False)
            self._log_turn("dictation_dropped", None, None, None, 0)
            self._transition(S.IDLE, "button_off_external")
        else:
            log.debug("dictation change ignored (state=%s is_on=%s)", self.state.value, is_on)

    def on_box_change(self, text: str) -> None:
        if self.state is not S.DICTATING:
            log.debug("box change ignored — state=%s", self.state.value)
            return
        log.debug("box change (%d chars): %r", len(text or ""), text)
        # send/cancel/stop (trailing fixed word) first; then press-by-name (verb + label arg).
        m = self.commands.match(text) or self.commands.match_press(text)
        if m is not None:
            log.info("command matched: %s (prefix=%s, target=%r, strip=%d)",
                     m.action, m.prefix, m.target, m.strip_len)
            self._act(m, text)

    def tick(self) -> None:
        # No silence timeout while DICTATING: the button is the sole authority for the
        # dictation lifetime. A turn ends only on a command word or a button-off event
        # (the OS/user turning the mic off → AXTitleChanged → on_dictation_change). The
        # arm-timeout below still backstops the ARMED gap (press produced no button-on event).
        now = self._mono()
        if self.state is S.ARMED and self._arm_ts is not None:
            armed = now - self._arm_ts
            if armed > self.cfg.arm_timeout_s:
                log.warning("arm timeout %.1fs > %.1fs — dictation never started, disarming",
                            armed, self.cfg.arm_timeout_s)
                self.ax.stop_observing_dictation()
                self._beep("error")
                self._resolve_wake(False)
                self._transition(S.IDLE, "arm_timeout")

    # ---- internals -------------------------------------------------------
    def _enter_dictating(self) -> None:
        """Confirmed dictation ON (button = truth) → observe the box for command words."""
        self._transition(S.DICTATING, "dictation_started")
        self._beep("listening")
        if not self.ax.observe_box(self.on_box_change):
            log.error("observe_box failed — box changes won't be seen; "
                      "turn will fall through to the silence backstop")
        else:
            log.info("dictation ON (button verified) — observing box for command word")

    def _act(self, m, text: str) -> None:
        """Sequence a matched command: ACTING → Actions.perform → telemetry → IDLE.

        `Actions` owns the keystroke/AX choreography; this method owns state
        sequencing, the outcome beep, wake resolution, turn logging, and the
        cross-turn correction window.
        """
        self._transition(S.ACTING, f"match_{m.action}")
        self.ax.stop_observing_dictation()  # we drive the mic-off now; not an external toggle
        out = self.actions.perform(m, text)

        self._beep(out.beep)
        self._resolve_wake(True)
        turn_id = self._log_turn(out.outcome, m.action, m.prefix, text, out.strip, box_post=out.box_post)
        log.info("dispatched %s: box_post=%r strip=%d turn=%s",
                 out.outcome, out.box_post, out.strip, turn_id)

        if out.outcome in ("cancelled", "stopped") and self._last_send is not None:
            tid, ts = self._last_send
            dt = (self._mono() - ts) * 1000.0
            if dt <= self.cfg.correction_window_ms:
                log.info("correction: turn %s undone by '%s' within %.0fms (likely misfire)",
                         tid, m.action, dt)
                self.tel.log_correction(tid, m.action, dt)
        if out.outcome == "sent":
            self._last_send = (turn_id, self._mono())
        self._transition(S.IDLE, f"dispatched_{out.outcome}")

    def _transition(self, new: S, reason: str) -> None:
        """The single state-change chokepoint (HARDENING-PLAN Phase 1).

        Asserts the (old → new) edge is declared in LEGAL, logs a state_transition
        telemetry record (with `illegal` set on a violation), fires the one
        state-change beep (entering ARMED), then commits the new state. In strict
        (test) builds an illegal transition raises; in prod it is logged, not fatal.
        """
        old = self.state
        legal = new in LEGAL.get(old, frozenset())
        self.tel.log_transition(old.value, new.value, reason, self._mono(), illegal=not legal)
        if not legal:
            log.warning("ILLEGAL transition %s → %s (%s)", old.value, new.value, reason)
            if self._strict:
                raise IllegalTransition(f"illegal transition {old.value} -> {new.value} ({reason})")
        else:
            log.debug("transition %s → %s (%s)", old.value, new.value, reason)
        self.state = new
        if new is S.ARMED:
            self._beep("armed")  # outcome beeps (send/cancel/stop/error) stay at their call sites

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
