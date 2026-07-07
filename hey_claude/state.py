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
from .appstate import AppState, AppStateMachine
from .bootstrap import BootstrapResult
from .commands import Fixups
from .dialog import DialogBox

log = logging.getLogger(__name__)


class S(Enum):
    IDLE = "idle"
    ARMED = "armed"
    DICTATING = "dictating"
    ACTING = "acting"
    DIALOG = "dialog"    # a Claude approval/choice box is open (reactive, foreground-gated)


# Declared legal transitions — the single source of truth for state changes.
# Every value in `_transition` is asserted against this table (HARDENING-PLAN Phase 1).
# DICTATING → ACTING on a command match; ACTING → IDLE once Actions.perform runs.
# DICTATING → IDLE remains for the silence/arm-timeout backstops (no dispatch).
# S.DIALOG is reactive (Claude-initiated), entered only from IDLE (a box appearing
# mid-DICTATING is masked — loophole #7). DIALOG→DIALOG is the wake-refresh self-edge
# (re-raise + re-announce). Resolve (DIALOG→IDLE) is foreground-gated in on_dialog_change.
LEGAL: dict[S, set[S]] = {
    S.IDLE: {S.ARMED, S.DIALOG},
    S.ARMED: {S.IDLE, S.DICTATING},
    S.DICTATING: {S.IDLE, S.ACTING},
    S.ACTING: {S.IDLE},
    S.DIALOG: {S.IDLE, S.DIALOG},
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
        appstate: Optional[AppStateMachine] = None,
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
        # The foreground gate (§3a). Injected so tests drive it; defaults to a fresh machine.
        # The dialog-resolve path reads is_foreground to tell "resolved" from "went blind".
        self.appstate = appstate or AppStateMachine()

        self.state = S.IDLE  # initial state — set directly; every later change goes via _transition
        self._wake_ts: Optional[float] = None
        self._arm_ts: Optional[float] = None
        self._boot: Optional[BootstrapResult] = None
        self._pending_wake: Optional[float] = None  # score of the accepted wake, awaiting resolution
        self._last_send: Optional[tuple] = None      # (turn_id, monotonic_ts)
        self._dialog: Optional[DialogBox] = None     # the currently-open box (stash), if S.DIALOG

    # ---- events ----------------------------------------------------------
    def on_wake(self, score: float) -> None:
        thr = self.cfg.wake_threshold
        accepted = score >= thr
        if self.state is S.DIALOG:
            # Wake in DIALOG is the escape hatch (§5) — NOT swallowed by the self-trigger
            # guard. It re-raises VS Code and re-evaluates the box (backstop for a missed
            # resolve event that would otherwise wedge the daemon deaf).
            if accepted:
                self._wake_in_dialog(score, thr)
            else:
                self.tel.log_wake(score, thr, False, followed_through=False)
            return
        if self.state is not S.IDLE:
            # self-trigger guard (FR-1): ignore wake while armed/dictating/acting.
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

        # Path B (§4): bootstrap raised VS Code, so a pending Claude box is now visible. If one
        # is open it covers the input — detect it BEFORE dictation (this also turns the old
        # "Voice-dictation button not found" abort into the correct branch). find_dialog retries
        # ~1.5s to outlast the ~560ms webview rebuild after the raise.
        if self.cfg.dialog_enabled:
            box = self.ax.find_dialog(settle_s=1.5)  # outlast the ~560ms webview rebuild post-raise
            if box is not None:
                log.info("Path-B: %s box present after raise → DIALOG (session=%r), no dictation",
                         box.type, box.session)
                self._resolve_wake(True)
                self._transition(S.IDLE, "dialog_redirect")  # unwind the arm; we're not dictating
                self._enter_dialog(box)
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

    def on_dialog_change(self, box: Optional[DialogBox]) -> None:
        """Ground-truth dialog state from the observer (or the reconciler / wake-refresh).

        Appear is trusted; RESOLVE (box→None) is **foreground-gated** — a box reads None both
        when it resolves AND when VS Code backgrounds and the webview tree collapses, so we only
        believe a resolve while the app is FOREGROUND (loophole #6). Mid-DICTATING appears are
        masked (loophole #7). Phase 1 is announce-only — no auto-answering yet.
        """
        if box is not None:
            if self.state is S.IDLE:
                self._enter_dialog(box)
            elif self.state is S.DIALOG:
                # A different box (session/type changed) while already in DIALOG → update stash.
                if box != self._dialog:
                    log.info("dialog changed while open: %r → %r", self._dialog, box)
                    self._dialog = box
                    self.tel.log_dialog("appear", box.type, len(box.options),
                                        self.appstate.is_foreground)
            else:
                log.debug("dialog appear ignored — state=%s (mid-turn mask)", self.state.value)
            return

        # box is None → candidate resolve.
        if self.state is not S.DIALOG:
            log.debug("dialog None ignored — state=%s (not in DIALOG)", self.state.value)
            return
        if not self.appstate.is_foreground:
            # Backgrounded, not resolved — hold DIALOG, never a false resolve (§5, loophole #6).
            log.info("dialog None while %s — holding DIALOG (blind, not a resolve)",
                     self.appstate.state.value)
            return
        self._resolve_dialog()

    def current_dialog(self) -> Optional[DialogBox]:
        """The open box stash — read by the reconciler to detect a stale/changed dialog."""
        return self._dialog

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

    # ---- dialog internals ------------------------------------------------
    def _enter_dialog(self, box: DialogBox) -> None:
        """IDLE → DIALOG: stash the box, announce it (beep), record telemetry."""
        self._dialog = box
        self._transition(S.DIALOG, "dialog_appeared")
        self._beep("dialog")
        self.tel.log_dialog("appear", box.type, len(box.options), self.appstate.is_foreground)
        log.info("DIALOG entered: %s box, %d option(s), session=%r",
                 box.type, len(box.options), box.session)

    def _resolve_dialog(self) -> None:
        """DIALOG → IDLE (foreground-verified by the caller). Clears the stash."""
        box = self._dialog
        self._dialog = None
        self.tel.log_dialog("resolve", box.type if box else None,
                            len(box.options) if box else None, True)
        self._transition(S.IDLE, "dialog_resolved")
        log.info("DIALOG resolved → IDLE")

    def _wake_in_dialog(self, score: float, thr: float) -> None:
        """Wake while a box is open: re-raise VS Code and re-evaluate (§5 escape hatch)."""
        self.tel.log_wake(score, thr, True, followed_through=True, note="dialog_refresh")
        boot = self.bootstrap.ensure_ready()  # raises VS Code → tree rebuilds
        if not boot.ok:
            log.error("wake-in-DIALOG: bootstrap failed — staying in DIALOG")
            self._beep("error")
            return
        box = self.ax.find_dialog(settle_s=1.5) if self.cfg.dialog_enabled else None
        if box is None:
            # None after a raise: resolve only if we're foreground (else still blind — hold).
            if self.appstate.is_foreground:
                log.info("wake-in-DIALOG: box gone (foreground) → resolving")
                self._resolve_dialog()
            else:
                log.info("wake-in-DIALOG: box None but %s — holding DIALOG",
                         self.appstate.state.value)
            return
        # Still a box → re-announce (and refresh the stash if it changed).
        self._dialog = box
        self._transition(S.DIALOG, "wake_refresh")  # self-edge breadcrumb
        self._beep("dialog")
        self.tel.log_dialog("refresh", box.type, len(box.options), self.appstate.is_foreground)
        log.info("wake-in-DIALOG: %s box still present → re-announced", box.type)

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
