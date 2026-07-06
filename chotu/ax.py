"""macOS Accessibility layer (AXPort impl). pyobjc.

Verified 2026-07-06: forcing AXManualAccessibility exposes the Claude input as an
AXTextArea whose AXValue is readable; an AXObserver on AXValueChanged is
event-driven and the element ref stays stable within a turn. Reads use a retry
loop (the webview re-renders and AX refs briefly go stale).
"""
from __future__ import annotations

import objc
from typing import Callable, Optional

from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue, AXObserverCreate, AXObserverAddNotification,
    AXObserverRemoveNotification, AXObserverGetRunLoopSource,
)
from CoreFoundation import (
    CFRunLoopGetCurrent, CFRunLoopAddSource, CFRunLoopRemoveSource,
    kCFRunLoopDefaultMode,
)


def _app_element(bundle_id: str):
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.bundleIdentifier() == bundle_id:
            pid = app.processIdentifier()
            return AXUIElementCreateApplication(pid), pid
    return None, None


class RealAX:
    def __init__(self, bundle_id: str = "com.microsoft.VSCode"):
        self.bundle_id = bundle_id
        self._observer = None
        self._source = None
        self._element = None
        self._cb: Optional[Callable[[str], None]] = None
        self._handler = None  # keep the closure alive

    def set_manual_a11y(self) -> None:
        el, _ = _app_element(self.bundle_id)
        if el is not None:
            AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)

    def _focused_textarea(self):
        el, pid = _app_element(self.bundle_id)
        if el is None:
            return None, None
        AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
        for _ in range(10):
            err, fe = AXUIElementCopyAttributeValue(el, "AXFocusedUIElement", None)
            if err == 0 and fe is not None:
                return fe, pid
        return None, pid

    def read_box(self) -> Optional[str]:
        fe, _ = self._focused_textarea()
        if fe is None:
            return None
        for _ in range(10):
            err, val = AXUIElementCopyAttributeValue(fe, "AXValue", None)
            if err == 0:
                return val
        return None

    def start_observing(self, on_change: Callable[[str], None]) -> bool:
        self.stop_observing()
        fe, pid = self._focused_textarea()
        if fe is None or pid is None:
            return False
        self._cb = on_change
        self._element = fe

        @objc.callbackFor(AXObserverCreate)
        def handler(observer, element, notification, refcon):
            err, val = AXUIElementCopyAttributeValue(element, "AXValue", None)
            if err == 0 and self._cb is not None:
                self._cb(val)

        self._handler = handler
        err, observer = AXObserverCreate(pid, handler, None)
        if err != 0 or observer is None:
            return False
        if AXObserverAddNotification(observer, fe, "AXValueChanged", None) != 0:
            return False
        self._observer = observer
        self._source = AXObserverGetRunLoopSource(observer)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), self._source, kCFRunLoopDefaultMode)
        return True

    def stop_observing(self) -> None:
        if self._observer is not None and self._element is not None:
            try:
                AXObserverRemoveNotification(self._observer, self._element, "AXValueChanged")
            except Exception:
                pass
        if self._source is not None:
            try:
                CFRunLoopRemoveSource(CFRunLoopGetCurrent(), self._source, kCFRunLoopDefaultMode)
            except Exception:
                pass
        self._observer = self._source = self._element = self._cb = self._handler = None
