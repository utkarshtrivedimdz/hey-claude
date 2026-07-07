"""Periodic reconciler — the backstop sweep (DIALOG-STATE-PLAN §5). Pure control logic.

Events (the AXObserver + NSWorkspace notifications) are the sensor and keep ~ms latency. But
event-driven detection drifts *silently* whenever an event is dropped — a wedged AXObserver, a
lost notification, a tree-rebuild race. This is the Kubernetes-style safety net: *observed UI =
desired, in-memory FSM = actual, snap actual→desired on mismatch*. It never detects first; it
only reconciles what events already should have set, bounding worst-case staleness to one tick.

It drives the FSMs **through their existing chokepoints** (`appstate.on_*`, `sm.on_dialog_change`)
— never a side door — so `LEGAL` and the foreground-gated-resolve rule still apply. Kept out of
`__main__` (which owns only the timer) so it's unit-testable with fakes + a real clock-free tick.

Constraints that keep it from re-opening a closed bug:
- Grounding `frontmost_bundle_id()` is a cheap NSWorkspace read, safe even while backgrounded, so
  it runs every tick and repairs a missed activate/deactivate in EITHER direction.
- The a11y reads (find_dialog, list_tabs) run ONLY while FOREGROUND — a background read is
  untrustworthy and would false-resolve (loophole #6).
- Idempotent: when in-memory == truth (the common case) the tick is a no-op.
"""
from __future__ import annotations

import logging

from .state import S

log = logging.getLogger(__name__)


class Reconciler:
    def __init__(self, cfg, system, ax, sm, appstate):
        self.cfg = cfg
        self.system = system
        self.ax = ax
        self.sm = sm
        self.appstate = appstate
        self.tabs: tuple = ()  # last Tabs snapshot (refreshed each foreground tick)

    def tick(self) -> None:
        # 1. Ground the foreground gate first (cheap, background-safe). Repairs a dropped
        #    activate/deactivate in either direction before we trust anything downstream.
        is_front = self.system.frontmost_bundle_id() == self.cfg.target_bundle_id
        if is_front and not self.appstate.is_foreground:
            log.info("reconcile: frontmost=%s but AppState=%s — missed activate",
                     self.cfg.target_bundle_id, self.appstate.state.value)
            self.appstate.on_activate()
        elif not is_front and self.appstate.is_foreground:
            log.info("reconcile: not frontmost but AppState=FOREGROUND — missed deactivate")
            self.appstate.on_deactivate()

        # 2 & 3 need a trustworthy a11y tree → only while FOREGROUND.
        if not self.appstate.is_foreground:
            return

        if self.cfg.dialog_enabled:
            # Single non-blocking scan (settle_s=0): the reconciler runs every N s, so a transient
            # mis-read self-heals on the next tick — no need to block the run loop retrying here.
            self._reconcile_dialog(self.ax.find_dialog(settle_s=0.0))
        # 3. Tabs is a pure snapshot — cheap wholesale overwrite (catches a missed tab/badge event).
        self.tabs = self.system.list_tabs()

    def _reconcile_dialog(self, box) -> None:
        """Compare UI truth (`box`) to what the turn FSM believes; snap via the chokepoint."""
        st = self.sm.state
        if box is not None and st is S.IDLE:
            log.info("reconcile: box present but state=IDLE — missed appear")
            self.sm.on_dialog_change(box)
        elif box is None and st is S.DIALOG:
            log.info("reconcile: state=DIALOG but box gone — missed resolve")
            self.sm.on_dialog_change(None)  # re-checks the foreground gate itself
        elif box is not None and st is S.DIALOG and box != self.sm.current_dialog():
            log.info("reconcile: DIALOG stash stale — correcting to current box")
            self.sm.on_dialog_change(box)
