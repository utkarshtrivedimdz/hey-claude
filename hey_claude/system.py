"""System / window control (SystemPort impl). pyobjc + `open`.

`code` CLI is not on PATH (verified), so we use `open`. Active-workspace detection
reads the VS Code window AXTitle (contains "geofast (Workspace)" when active).
"""
from __future__ import annotations

import logging
import re
import subprocess
from collections import deque
from typing import Optional, Tuple

from AppKit import NSWorkspace, NSWorkspaceApplicationKey
from ApplicationServices import (
    AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue,
)

from .appstate import AppStateMachine, Tab

log = logging.getLogger(__name__)

NSApplicationActivateIgnoringOtherApps = 1 << 1
_APP_NAME = "Visual Studio Code"

# Tab labels carry the blue-dot "needs attention" count as a trailing "… | N" (§3b).
_BADGE_RE = re.compile(r"\|\s*(\d+)\s*$")


def _get(el, attr):
    err, val = AXUIElementCopyAttributeValue(el, attr, None)
    return val if err == 0 else None


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
        # `open -a` goes through LaunchServices, which the OS honors as a user-initiated
        # foreground request even from a background process. NSRunningApplication's
        # activateWithOptions_(IgnoringOtherApps) is NOT honored cross-app on macOS 15
        # Sequoia — a backgrounded hey-claude could not pull VS Code in front of e.g. Firefox,
        # so the focus gate timed out and the turn aborted. `open -a` fixes that; we still
        # call activateWithOptions_ first as a cheap in-process nudge when already running.
        a = self._running_app()
        if a is not None:
            a.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        log.debug("raise_app: open -a %s (LaunchServices foreground)", _APP_NAME)
        subprocess.run(["open", "-a", _APP_NAME], check=False)

    # ---- tabs snapshot (§3b) --------------------------------------------
    def list_tabs(self) -> Tuple[Tab, ...]:
        """Read the editor AXTabGroup's radio children into a Tabs snapshot.

        Valid only while VS Code is FOREGROUND (a background window's tree may be stale) —
        the AppState-gated caller enforces that; this method just reads whatever is there.
        `active` = the radio's AXValue == 1; `badge` = the trailing "| N" attention count.
        `is_claude` is provisional (only the active Claude tab exposes a Message-input, so a
        background tab can't be confirmed from the radio alone) — Phase 4 refines it live.
        """
        a = self._running_app()
        if a is None:
            return ()
        ax = AXUIElementCreateApplication(a.processIdentifier())
        AXUIElementSetAttributeValue(ax, "AXManualAccessibility", True)
        group = _bfs_find(ax, lambda el: _get(el, "AXRole") == "AXTabGroup")
        if group is None:
            log.debug("list_tabs: no AXTabGroup found")
            return ()
        tabs = []
        for child in (_get(group, "AXChildren") or []):
            if _get(child, "AXRole") != "AXRadioButton":
                continue
            title = _get(child, "AXTitle") or ""
            m = _BADGE_RE.search(title)
            tabs.append(Tab(
                title=title,
                active=(_get(child, "AXValue") == 1),
                badge=int(m.group(1)) if m else 0,
                is_claude=False,
            ))
        log.debug("list_tabs: %d tab(s)", len(tabs))
        return tuple(tabs)


def _bfs_find(root, predicate) -> Optional[object]:
    """Bounded breadth-first walk → first element satisfying `predicate` (mirrors ax._bfs_find).

    BFS finds a shallow toolbar/tab control before descending VS Code's deep editor subtree; a
    predicate that raises on a malformed node is skipped, not fatal.
    """
    q = deque([root])
    seen = 0
    while q:
        el = q.popleft()
        seen += 1
        if seen > 40000:
            log.warning("_bfs_find: node budget hit (40000 nodes)")
            return None
        try:
            if predicate(el):
                return el
        except Exception as e:
            log.debug("_bfs_find: predicate raised on a node: %r", e)
        for c in (_get(el, "AXChildren") or []):
            q.append(c)
    return None


class AppFocusObserver:
    """Turns NSWorkspace app lifecycle notifications into AppStateMachine events (I/O half of §3a).

    Subscribes to Did{Activate,Deactivate,Launch,Terminate}Application for the target bundle id
    only, on the posting thread (main run loop — same thread all state mutations run on). `seed()`
    resolves the boot UNKNOWN from current reality so the FSM is correct before the first event.
    """

    _NOTES = {
        "NSWorkspaceDidActivateApplicationNotification": "on_activate",
        "NSWorkspaceDidDeactivateApplicationNotification": "on_deactivate",
        "NSWorkspaceDidLaunchApplicationNotification": "on_launch",
        "NSWorkspaceDidTerminateApplicationNotification": "on_terminate",
    }

    def __init__(self, cfg, system: RealSystem, machine: AppStateMachine):
        self.cfg = cfg
        self.system = system
        self.machine = machine
        self._tokens: list = []

    def seed(self) -> None:
        """Drive the FSM out of UNKNOWN using the current frontmost/running truth."""
        if not self.system.is_app_running():
            self.machine.on_terminate()
        elif self.system.frontmost_bundle_id() == self.cfg.target_bundle_id:
            self.machine.on_activate()
        else:
            self.machine.on_deactivate()

    def start(self) -> None:
        self.seed()
        nc = NSWorkspace.sharedWorkspace().notificationCenter()
        for name, method in self._NOTES.items():
            token = nc.addObserverForName_object_queue_usingBlock_(
                name, None, None, self._make_block(getattr(self.machine, method))
            )
            self._tokens.append(token)
        log.debug("AppFocusObserver: subscribed to %d NSWorkspace notifications", len(self._tokens))

    def _make_block(self, handler):
        target = self.cfg.target_bundle_id

        def block(note):
            info = note.userInfo()
            app = info.objectForKey_(NSWorkspaceApplicationKey) if info is not None else None
            if app is not None and app.bundleIdentifier() == target:
                handler()

        return block

    def stop(self) -> None:
        nc = NSWorkspace.sharedWorkspace().notificationCenter()
        for token in self._tokens:
            nc.removeObserver_(token)
        self._tokens = []
