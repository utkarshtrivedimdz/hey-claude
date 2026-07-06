"""System / window control (SystemPort impl). pyobjc + `open`.

`code` CLI is not on PATH (verified), so we use `open`. Active-workspace detection
reads the VS Code window AXTitle (contains "geofast (Workspace)" when active).
"""
from __future__ import annotations

import logging
import subprocess
from typing import Optional

from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue,
)

log = logging.getLogger(__name__)

NSApplicationActivateIgnoringOtherApps = 1 << 1
_APP_NAME = "Visual Studio Code"


class RealSystem:
    def __init__(self, cfg):
        self.cfg = cfg

    def _running_app(self):
        for a in NSWorkspace.sharedWorkspace().runningApplications():
            if a.bundleIdentifier() == self.cfg.target_bundle_id:
                return a
        return None

    def frontmost_bundle_id(self) -> Optional[str]:
        f = NSWorkspace.sharedWorkspace().frontmostApplication()
        return f.bundleIdentifier() if f is not None else None

    def window_title(self) -> Optional[str]:
        a = self._running_app()
        if a is None:
            return None
        ax = AXUIElementCreateApplication(a.processIdentifier())
        AXUIElementSetAttributeValue(ax, "AXManualAccessibility", True)
        for attr in ("AXFocusedWindow", "AXMainWindow"):
            err, w = AXUIElementCopyAttributeValue(ax, attr, None)
            if err == 0 and w is not None:
                e2, title = AXUIElementCopyAttributeValue(w, "AXTitle", None)
                if e2 == 0 and title:
                    log.debug("window_title (%s): %r", attr, title)
                    return title
        log.debug("window_title: no AXTitle on focused/main window")
        return None

    def is_app_running(self) -> bool:
        return self._running_app() is not None

    def launch_app(self) -> None:
        log.info("launching %s (open -n -a)", _APP_NAME)
        r = subprocess.run(["open", "-n", "-a", _APP_NAME], check=False, stderr=subprocess.PIPE)
        if r.returncode != 0:
            log.error("launch_app failed (rc=%s): %s", r.returncode,
                      (r.stderr or b"").decode(errors="replace").strip())

    def open_path(self, path: str) -> None:
        log.info("open path: %s", path)
        r = subprocess.run(["open", path], check=False, stderr=subprocess.PIPE)
        if r.returncode != 0:
            log.error("open_path failed (rc=%s): %s", r.returncode,
                      (r.stderr or b"").decode(errors="replace").strip())

    def raise_app(self) -> None:
        a = self._running_app()
        if a is not None:
            log.debug("raise_app: activating running instance")
            a.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        else:
            log.debug("raise_app: no running instance — open -a")
            subprocess.run(["open", "-a", _APP_NAME], check=False)
