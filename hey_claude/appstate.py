"""Environment state — VS Code foreground/background as a correctness gate (Phase 0).

Foreground/background is **orthogonal** to the turn phase (`state.py`'s `S`): you can be
`ARMED` while VS Code is foreground, or `IDLE` while it's backgrounded. So it's a *separate*
small FSM, not folded into `S`. Its whole job is to answer one question the dialog layer
depends on for correctness: **is VS Code stably foreground right now?** — because the Claude
panel's a11y tree collapses (`find_dialog → None`) the instant another app takes foreground,
which is indistinguishable from "the box resolved" unless we know we went blind. See
docs/DIALOG-STATE-PLAN.md §0, §3a.

Same rigor as `state.py`: a `LEGAL` table and a single `_transition` chokepoint, with logged
transitions so "went blind at T" is greppable. The FSM logic here is **pure** (no pyobjc) and
unit-tested with a fake feeding events; the I/O observer that turns `NSWorkspace`
`DidActivate/DidDeactivate/DidLaunch/DidTerminate` notifications into these event calls lives
in `system.py`.

`Tabs` (below) is deliberately NOT a machine — the open-tab set is dynamic and we can't drive
internal states for background tabs, only read their badge. It's a plain observed snapshot
(§3b); `tab_status` is a pure projection over it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

log = logging.getLogger(__name__)


class AppState(Enum):
    UNKNOWN = "unknown"          # boot, before the first NSWorkspace observation
    NOT_RUNNING = "not_running"  # target app is not launched
    BACKGROUND = "background"    # running, but not frontmost — a11y tree untrustworthy
    FOREGROUND = "foreground"    # running and frontmost — a11y reads are ground truth


# Declared legal edges — the single source of truth for AppState changes (mirrors
# state.py's LEGAL). Matches the DIALOG-STATE-PLAN §3a state diagram exactly:
#   UNKNOWN → any (first observation resolves the boot unknown)
#   NOT_RUNNING → BACKGROUND (DidLaunch / bootstrap `open -a`; DidActivate then raises it FG)
#   BACKGROUND ⇄ FOREGROUND (DidActivate / DidDeactivate)
#   {BACKGROUND, FOREGROUND} → NOT_RUNNING (DidTerminate)
LEGAL: dict[AppState, set[AppState]] = {
    AppState.UNKNOWN: {AppState.FOREGROUND, AppState.BACKGROUND, AppState.NOT_RUNNING},
    AppState.NOT_RUNNING: {AppState.BACKGROUND},
    AppState.BACKGROUND: {AppState.FOREGROUND, AppState.NOT_RUNNING},
    AppState.FOREGROUND: {AppState.BACKGROUND, AppState.NOT_RUNNING},
}


class IllegalTransition(Exception):
    """Raised (in strict/test builds) when a transition is not in LEGAL."""


class AppStateMachine:
    """Pure FSM over VS Code focus/lifecycle. Fed events by system.py's observer.

    Events map 1:1 to NSWorkspace notifications for the target bundle id:
      on_activate   ← DidActivateApplication   → FOREGROUND
      on_deactivate ← DidDeactivateApplication → BACKGROUND
      on_launch     ← DidLaunchApplication     → BACKGROUND (activate follows if it's frontmost)
      on_terminate  ← DidTerminateApplication  → NOT_RUNNING

    `on_change(old, new)` (optional) fires after every committed transition so the runtime can
    attach/detach the dialog observer + refresh Tabs on →FOREGROUND / →BACKGROUND (Phase 1).
    Kept out of the FSM's own logic so this module stays pure and dependency-free.
    """

    def __init__(
        self,
        on_change: Optional[Callable[[AppState, AppState], None]] = None,
        strict: bool = False,
    ):
        self._on_change = on_change
        self._strict = strict  # test builds raise on an illegal transition; prod only logs
        self.state = AppState.UNKNOWN  # set directly; every later change goes via _transition

    # ---- events ----------------------------------------------------------
    def on_activate(self) -> None:
        self._transition(AppState.FOREGROUND, "did_activate")

    def on_deactivate(self) -> None:
        self._transition(AppState.BACKGROUND, "did_deactivate")

    def on_launch(self) -> None:
        # Launch means the process exists but isn't necessarily frontmost yet — a DidActivate
        # follows when it comes forward. Landing in BACKGROUND keeps the "reads untrustworthy
        # until FOREGROUND" invariant honest.
        self._transition(AppState.BACKGROUND, "did_launch")

    def on_terminate(self) -> None:
        self._transition(AppState.NOT_RUNNING, "did_terminate")

    # ---- queries ---------------------------------------------------------
    @property
    def is_foreground(self) -> bool:
        """The single correctness gate the dialog layer reads before trusting an a11y read."""
        return self.state is AppState.FOREGROUND

    # ---- internals -------------------------------------------------------
    def _transition(self, new: AppState, reason: str) -> None:
        """The single state-change chokepoint (mirrors state.py._transition).

        Duplicate same-state events (NSWorkspace can deliver these) are idempotent no-ops, not
        illegal transitions. Otherwise: assert the edge is in LEGAL, log it (greppable), commit,
        then fire on_change. In strict (test) builds an illegal edge raises; in prod it's logged
        and still committed so the FSM tracks reality even after a missed intermediate event.
        """
        old = self.state
        if new is old:
            log.debug("appstate: %s already (%s) — no-op", new.value, reason)
            return
        legal = new in LEGAL.get(old, frozenset())
        if not legal:
            log.warning("appstate: ILLEGAL transition %s → %s (%s)", old.value, new.value, reason)
            if self._strict:
                raise IllegalTransition(f"illegal transition {old.value} -> {new.value} ({reason})")
        else:
            log.info("appstate: %s → %s (%s)", old.value, new.value, reason)
        self.state = new
        if self._on_change is not None:
            self._on_change(old, new)


# ======================================================================
# Tabs — an observed snapshot (§3b), NOT a state machine.
# ======================================================================

class TabStatus(Enum):
    ACTIVE = "active"              # the frontmost tab
    BG_QUIET = "bg_quiet"          # a background tab with no attention badge
    BG_ATTENTION = "bg_attention"  # a background tab whose blue-dot badge > 0 (needs you)


@dataclass(frozen=True)
class Tab:
    title: str
    active: bool
    badge: int          # the blue-dot "needs attention" count parsed from the tab label (… | N)
    is_claude: bool     # does this tab host a Claude session? (has a Message-input textarea)


# Tabs = tuple[Tab, ...] — a point-in-time read of the AXTabGroup, valid only while FOREGROUND.


def tab_status(tab: Tab) -> TabStatus:
    """Pure projection of a Tab's derived attention status (powers Phase-4 'session X needs you')."""
    if tab.active:
        return TabStatus.ACTIVE
    return TabStatus.BG_ATTENTION if tab.badge > 0 else TabStatus.BG_QUIET
