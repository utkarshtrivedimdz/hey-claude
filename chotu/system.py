"""System / window control (SystemPort impl). pyobjc + `open`.

`code` CLI is not on PATH (verified), so we use `open`. Active-workspace detection
reads the VS Code window AXTitle (contains "geofast (Workspace)" when active).
"""
from __future__ import annotations

import subprocess
from typing import Optional

from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue,
)

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
                    return title
        return None

    def is_app_running(self) -> bool:
        return self._running_app() is not None

    def launch_app(self) -> None:
        subprocess.run(["open", "-n", "-a", _APP_NAME], check=False)

    def open_path(self, path: str) -> None:
        subprocess.run(["open", path], check=False)

    def raise_app(self) -> None:
        a = self._running_app()
        if a is not None:
            a.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        else:
            subprocess.run(["open", "-a", _APP_NAME], check=False)
