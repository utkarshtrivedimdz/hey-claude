"""Bootstrap + focus-safety gate (FR-0 step 1, FR-3).

Idempotent: launch VS Code only if down, open the geofast workspace only if it
isn't active, raise to front, then VERIFY frontmost==VS Code and title matches
before anyone injects a keystroke. Pure control flow over a SystemPort so it's
unit-testable with a fake (ARCHITECTURE §7.2).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from .ports import SystemPort

log = logging.getLogger(__name__)


@dataclass
class BootstrapResult:
    ok: bool
    cold_start: bool
    ms: int
    focus_gate: str  # "pass" | "raised" | "abort"


class Bootstrap:
    def __init__(
        self,
        system: SystemPort,
        cfg,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        ready_timeout_s: float = 12.0,
    ):
        self.system = system
        self.cfg = cfg
        self._mono = monotonic
        self._sleep = sleep
        self._ready_timeout = ready_timeout_s

    def _title_active(self) -> bool:
        sub = self.cfg.target_title_substr
        # empty substring would match any window → fail closed until the target is configured.
        return bool(sub) and sub in (self.system.window_title() or "")

    def _focus_ok(self) -> bool:
        return (
            self.system.frontmost_bundle_id() == self.cfg.target_bundle_id
            and self._title_active()
        )

    def ensure_ready(self) -> BootstrapResult:
        t0 = self._mono()
        cold = False

        if not self.system.is_app_running():
            cold = True
            log.info("bootstrap: %s not running — cold start (launch + open workspace)",
                     self.cfg.target_bundle_id)
            self.system.launch_app()
            self.system.open_path(self.cfg.target_workspace)
        elif not self._title_active():
            log.info("bootstrap: target window not active — opening workspace %s",
                     self.cfg.target_workspace)
            self.system.open_path(self.cfg.target_workspace)
        else:
            log.debug("bootstrap: %s already running with target window active", self.cfg.target_bundle_id)

        self.system.raise_app()

        # Poll for the focus gate to pass (cold start needs a few seconds).
        deadline = t0 + self._ready_timeout
        focus_gate = "abort"
        polls = 0
        while self._mono() < deadline:
            if self._focus_ok():
                focus_gate = "pass" if not cold else "raised"
                break
            polls += 1
            log.debug("bootstrap: focus gate not yet satisfied (poll %d) — re-raising", polls)
            self.system.raise_app()
            self._sleep(0.25)

        ms = int((self._mono() - t0) * 1000)
        if focus_gate == "abort":
            log.error("bootstrap: focus gate never passed after %dms (front=%r, title_active=%s) — abort",
                      ms, self.system.frontmost_bundle_id(), self._title_active())
        else:
            log.info("bootstrap ready: focus_gate=%s cold=%s %dms", focus_gate, cold, ms)
        return BootstrapResult(ok=(focus_gate != "abort"), cold_start=cold, ms=ms, focus_gate=focus_gate)
