"""macOS Accessibility layer (AXPort impl). pyobjc.

Verified 2026-07-06: forcing AXManualAccessibility exposes the Claude input as an
AXTextArea whose AXValue is readable; an AXObserver on AXValueChanged is
event-driven and the element ref stays stable within a turn. Reads use a retry
loop (the webview re-renders and AX refs briefly go stale).

Dictation (2026-07-06): the Claude panel's Voice-dictation button is the ground
truth for whether recording is on. It's an AXButton with AXPress; its AXDescription
is "Voice dictation" when off and "Stop recording" when on (blue), and it emits
AXTitleChanged on every toggle — so a second AXObserver on it drives the DICTATING
state event-driven, with no polling. See DICTATION-AX-PLAN.md.
"""
from __future__ import annotations

import logging
import time
import objc
from typing import Callable, Optional

from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue, AXUIElementPerformAction,
    AXObserverCreate, AXObserverAddNotification,
    AXObserverRemoveNotification, AXObserverGetRunLoopSource,
)
from CoreFoundation import (
    CFRunLoopGetCurrent, CFRunLoopAddSource, CFRunLoopRemoveSource,
    kCFRunLoopDefaultMode,
)

log = logging.getLogger(__name__)


def _app_element(bundle_id: str):
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.bundleIdentifier() == bundle_id:
            pid = app.processIdentifier()
            return AXUIElementCreateApplication(pid), pid
    return None, None


def _get(el, attr):
    err, val = AXUIElementCopyAttributeValue(el, attr, None)
    return val if err == 0 else None


class _Observer:
    """Holds one AXObserver + its run-loop source + observed element + handler.

    Lets the box (AXValueChanged) and dictation (AXTitleChanged) observers coexist
    without duplicated create/teardown logic. `detach()` is idempotent.
    """

    def __init__(self, name: str):
        self.name = name
        self._observer = None
        self._source = None
        self._element = None
        self._handler = None   # keep the closure alive
        self._notif = None

    def attach(self, pid, element, notif, handler) -> bool:
        self.detach()
        err, observer = AXObserverCreate(pid, handler, None)
        if err != 0 or observer is None:
            log.error("%s: AXObserverCreate failed (err=%s)", self.name, err)
            return False
        if AXObserverAddNotification(observer, element, notif, None) != 0:
            log.error("%s: AXObserverAddNotification(%s) failed", self.name, notif)
            return False
        self._observer, self._element, self._handler, self._notif = observer, element, handler, notif
        self._source = AXObserverGetRunLoopSource(observer)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), self._source, kCFRunLoopDefaultMode)
        log.debug("%s: AXObserver attached (pid=%s, %s)", self.name, pid, notif)
        return True

    def detach(self) -> None:
        if self._observer is not None and self._element is not None and self._notif is not None:
            try:
                AXObserverRemoveNotification(self._observer, self._element, self._notif)
            except Exception:
                log.exception("%s: AXObserverRemoveNotification raised", self.name)
        if self._source is not None:
            try:
                CFRunLoopRemoveSource(CFRunLoopGetCurrent(), self._source, kCFRunLoopDefaultMode)
            except Exception:
                pass
        self._observer = self._source = self._element = self._handler = self._notif = None


class RealAX:
    def __init__(self, cfg):
        self.cfg = cfg
        self.bundle_id = cfg.target_bundle_id
        self._dict_off = cfg.dictation_button_desc_off
        self._dict_on = cfg.dictation_button_desc_on
        self._box_obs = _Observer("observe_box")
        self._dict_obs = _Observer("observe_dictation")
        self._box_cb: Optional[Callable[[str], None]] = None
        self._dict_cb: Optional[Callable[[bool], None]] = None

    def set_manual_a11y(self) -> None:
        el, _ = _app_element(self.bundle_id)
        if el is not None:
            AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
            log.debug("set AXManualAccessibility on %s", self.bundle_id)
        else:
            log.warning("set_manual_a11y: %s not running — a11y tree not unlocked", self.bundle_id)

    # ---- element lookup --------------------------------------------------
    def _focused_textarea(self):
        el, pid = _app_element(self.bundle_id)
        if el is None:
            log.warning("_focused_textarea: %s not running", self.bundle_id)
            return None, None
        AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
        for attempt in range(10):
            err, fe = AXUIElementCopyAttributeValue(el, "AXFocusedUIElement", None)
            if err == 0 and fe is not None:
                if attempt:
                    log.debug("focused element resolved after %d retries", attempt)
                return fe, pid
        log.warning("_focused_textarea: no AXFocusedUIElement after 10 tries (ref may be stale)")
        return None, pid

    def _find_element(self, root, role: str, descriptions) -> Optional[object]:
        """Breadth-first tree walk → first element matching role + any AXDescription.

        BFS (not DFS) so a shallow panel control is found before descending into VS
        Code's very deep editor subtree — DFS can exhaust the node budget first.
        Reusable for future press-a-button-by-name commands (BACKLOG press-by-name).
        """
        from collections import deque
        queue = deque([root])
        seen = 0
        while queue:
            el = queue.popleft()
            seen += 1
            if seen > 40000:
                log.warning("_find_element: node budget hit before finding %r", descriptions)
                return None
            if _get(el, "AXRole") == role and _get(el, "AXDescription") in descriptions:
                return el
            for c in (_get(el, "AXChildren") or []):
                queue.append(c)
        return None

    def _dictation_button(self):
        el, pid = _app_element(self.bundle_id)
        if el is None:
            return None, None
        AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
        # The button lives in a webview whose AX tree is briefly stale after a re-render;
        # retry a few times (mirrors _focused_textarea) before concluding it's absent.
        for attempt in range(6):
            btn = self._find_element(el, "AXButton", {self._dict_off, self._dict_on})
            if btn is not None:
                if attempt:
                    log.debug("dictation button resolved after %d retries", attempt)
                return btn, pid
            time.sleep(0.05)
        return None, pid

    # ---- box (textarea) --------------------------------------------------
    def read_box(self) -> Optional[str]:
        fe, _ = self._focused_textarea()
        if fe is None:
            return None
        for _ in range(10):
            err, val = AXUIElementCopyAttributeValue(fe, "AXValue", None)
            if err == 0:
                log.debug("read_box: %r", val)
                return val
        log.warning("read_box: AXValue unreadable after 10 tries")
        return None

    def read_box_settled(self, timeout_s: float = 1.5, poll_s: float = 0.1) -> Optional[str]:
        """Read the box, waiting out the post-dictation refill race.

        When dictation is stopped via the button, the box transiently EMPTIES and then the
        finalized transcription re-commits a moment later (async). Acting during that empty
        window silently drops the send (backspace/Return hit nothing). This polls read_box
        until it's non-empty (not a placeholder) and stable across two consecutive reads, or
        until timeout — so the caller only touches the box once the text has re-settled.
        """
        placeholders = self.cfg.placeholders or []

        def is_empty(b) -> bool:
            return (not b) or (b in placeholders)

        prev = self.read_box()
        waited = 0.0
        while waited < timeout_s:
            time.sleep(poll_s)
            waited += poll_s
            cur = self.read_box()
            log.debug("read_box_settled: t=%.2f box=%r", waited, cur)
            if not is_empty(cur) and cur == prev:
                log.debug("read_box_settled: stable after %.2fs → %r", waited, cur)
                return cur
            prev = cur
        log.warning("read_box_settled: not stable after %.1fs (last=%r)", timeout_s, prev)
        return prev

    def observe_box(self, on_change: Callable[[str], None]) -> bool:
        fe, pid = self._focused_textarea()
        if fe is None or pid is None:
            log.error("observe_box: no focused textarea (pid=%s) — cannot observe box", pid)
            return False
        self._box_cb = on_change

        @objc.callbackFor(AXObserverCreate)
        def handler(observer, element, notification, refcon):
            err, val = AXUIElementCopyAttributeValue(element, "AXValue", None)
            if err == 0 and self._box_cb is not None:
                log.debug("AXValueChanged → %r", val)
                self._box_cb(val)
            elif err != 0:
                log.warning("AXValueChanged fired but AXValue read failed (err=%s)", err)

        return self._box_obs.attach(pid, fe, "AXValueChanged", handler)

    def stop_observing_box(self) -> None:
        self._box_cb = None
        self._box_obs.detach()

    # ---- dictation button (ground truth) ---------------------------------
    def dictation_on(self) -> Optional[bool]:
        btn, _ = self._dictation_button()
        if btn is None:
            log.warning("dictation_on: %r button not found", self._dict_off)
            return None
        return _get(btn, "AXDescription") == self._dict_on

    def press_dictation(self) -> None:
        btn, _ = self._dictation_button()
        if btn is None:
            log.warning("press_dictation: %r button not found — cannot toggle", self._dict_off)
            return
        AXUIElementPerformAction(btn, "AXPress")
        log.debug("press_dictation: AXPress sent")

    def observe_dictation(self, on_change: Callable[[bool], None]) -> bool:
        btn, pid = self._dictation_button()
        if btn is None or pid is None:
            log.error("observe_dictation: button not found (pid=%s)", pid)
            return False
        self._dict_cb = on_change
        on_label = self._dict_on

        @objc.callbackFor(AXObserverCreate)
        def handler(observer, element, notification, refcon):
            desc = _get(element, "AXDescription")
            if self._dict_cb is not None:
                is_on = (desc == on_label)
                log.debug("AXTitleChanged → desc=%r is_on=%s", desc, is_on)
                self._dict_cb(is_on)

        return self._dict_obs.attach(pid, btn, "AXTitleChanged", handler)

    def stop_observing_dictation(self) -> None:
        self._dict_cb = None
        self._dict_obs.detach()
