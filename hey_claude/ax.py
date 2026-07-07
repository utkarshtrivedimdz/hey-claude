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

from .dialog import DialogBox, NUMBERED, classify

log = logging.getLogger(__name__)

_UNSCANNED = object()  # signature sentinel: observer hasn't scanned yet (distinct from None)


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
        self._dialog_obs = _Observer("observe_dialog")
        self._box_cb: Optional[Callable[[str], None]] = None
        self._dict_cb: Optional[Callable[[bool], None]] = None
        self._dialog_cb: Optional[Callable[[Optional[DialogBox]], None]] = None
        self._last_dialog_sig = _UNSCANNED  # signature-debounce for the dialog observer

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

    def _bfs_find(self, root, predicate) -> Optional[object]:
        """Breadth-first tree walk → first element satisfying `predicate`.

        BFS (not DFS) so a shallow panel control is found before descending into VS
        Code's very deep editor subtree — DFS can exhaust the node budget first. A
        predicate that raises on a weird node is skipped, not fatal.
        """
        from collections import deque
        queue = deque([root])
        seen = 0
        while queue:
            el = queue.popleft()
            seen += 1
            if seen > 40000:
                log.warning("_bfs_find: node budget hit (40000 nodes)")
                return None
            try:
                if predicate(el):
                    return el
            except Exception as e:  # a malformed node must not abort the whole walk
                log.debug("_bfs_find: predicate raised on a node: %r", e)
            for c in (_get(el, "AXChildren") or []):
                queue.append(c)
        return None

    def _find_element(self, root, role: str, descriptions) -> Optional[object]:
        """First element matching an exact role + one of `descriptions` (AXDescription)."""
        return self._bfs_find(
            root, lambda el: _get(el, "AXRole") == role and _get(el, "AXDescription") in descriptions
        )

    def press_by_name(self, keyword: str, roles=("AXButton", "AXRadioButton")) -> bool:
        """Find the first pressable control whose label CONTAINS `keyword` and AXPress it.

        Label = AXTitle | AXDescription | AXValue, matched case-insensitively as a
        substring (so "submit" hits the "Submit answers" button, "yes" hits a "Yes"
        radio option). Only the ACTIVE Claude Code tab is in the AX tree — a control
        in a background tab is invisible and this returns False (BACKLOG: focus the
        tab first). Returns True iff a match was found and AXPress was sent.
        """
        kw = (keyword or "").strip().lower()
        if not kw:
            return False
        el, _ = _app_element(self.bundle_id)
        if el is None:
            log.warning("press_by_name: %s not running", self.bundle_id)
            return False
        AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
        roleset = set(roles)

        def matches(node) -> bool:
            if _get(node, "AXRole") not in roleset:
                return False
            for attr in ("AXTitle", "AXDescription", "AXValue"):
                v = _get(node, attr)
                if isinstance(v, str) and kw in v.lower():
                    return True
            return False

        # The webview AX tree is briefly stale right after a dialog renders — retry a
        # few times before concluding the control is absent (mirrors _dictation_button).
        for attempt in range(6):
            target = self._bfs_find(el, matches)
            if target is not None:
                label = _get(target, "AXTitle") or _get(target, "AXDescription") or _get(target, "AXValue")
                err = AXUIElementPerformAction(target, "AXPress")
                log.info("press_by_name: AXPress %r → %r (role=%s, err=%s)",
                         kw, label, _get(target, "AXRole"), err)
                return True
            time.sleep(0.05)
        log.warning("press_by_name: no %s element label contains %r", tuple(roles), kw)
        return False

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

    # ---- dialog detection (Phase 1, per-session scan) --------------------
    # NOTE: the per-session-container scan below was designed from a live probe (DIALOG-STATE-PLAN
    # §6) but its probe scripts were not committed — re-verify end-to-end on a running VS Code with
    # a real approval AND a split-pane choice box before trusting it (Phase-1 handover item).

    def _session_title(self, node) -> Optional[str]:
        """Nearest ancestor AXGroup with a non-empty AXTitle = the owning session container (§6).

        Walks up via AXParent. Stops at the FIRST titled AXGroup — that's the per-session
        boundary, below the shared workbench AXWebArea (whose title is the active editor and must
        NOT be used to scope, §6). Bounded so a cycle/deep tree can't spin.
        """
        cur = node
        for _ in range(60):
            cur = _get(cur, "AXParent")
            if cur is None:
                return None
            if _get(cur, "AXRole") == "AXGroup":
                title = _get(cur, "AXTitle")
                if title:
                    return title
        return None

    def _under_tabgroup(self, node) -> bool:
        """True if `node` is an editor TAB (an AXRadioButton inside an AXTabGroup), not a choice
        radio. Tabs share the AXRadioButton role; excluding them keeps classify from mis-reading a
        tab strip as a choice box. Stops at the session AXGroup so a real choice radio returns False.
        """
        cur = node
        for _ in range(8):
            cur = _get(cur, "AXParent")
            if cur is None:
                return False
            role = _get(cur, "AXRole")
            if role == "AXTabGroup":
                return True
            if role == "AXGroup" and _get(cur, "AXTitle"):
                return False
        return False

    def _scan_dialogs(self) -> Optional[DialogBox]:
        """One pass: group numbered buttons + non-tab radios by session container → classify each.

        Returns the FOCUSED session's box if it has one; else the first other visible session's box
        (attribution preserved on DialogBox.session — a box in a non-focused split pane is still
        detected). No cross-pane merge: every control is bucketed by its own session title.
        """
        el, _ = _app_element(self.bundle_id)
        if el is None:
            return None
        AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
        focused = _get(el, "AXFocusedUIElement")
        focused_session = self._session_title(focused) if focused is not None else None

        groups: dict[str, list[dict]] = {}
        from collections import deque
        q = deque([el])
        seen = 0
        while q:
            node = q.popleft()
            seen += 1
            if seen > 40000:
                log.warning("_scan_dialogs: node budget hit (40000 nodes)")
                break
            try:
                role = _get(node, "AXRole")
                if role == "AXButton":
                    label = _get(node, "AXTitle") or _get(node, "AXDescription")
                    if label and NUMBERED.match(label):
                        sess = self._session_title(node)
                        if sess:
                            groups.setdefault(sess, []).append({
                                "role": "AXButton", "label": label,
                                "enabled": bool(_get(node, "AXEnabled")), "selected": False,
                            })
                elif role == "AXRadioButton" and not self._under_tabgroup(node):
                    label = _get(node, "AXTitle") or _get(node, "AXValue")
                    sess = self._session_title(node)
                    if sess:
                        groups.setdefault(sess, []).append({
                            "role": "AXRadioButton",
                            "label": label if isinstance(label, str) else "",
                            "enabled": bool(_get(node, "AXEnabled")),
                            "selected": _get(node, "AXValue") == 1,
                        })
            except Exception as e:
                log.debug("_scan_dialogs: node raised, skipped: %r", e)
            for c in (_get(node, "AXChildren") or []):
                q.append(c)

        boxes = {s: classify(raw, session=s) for s, raw in groups.items()}
        boxes = {s: b for s, b in boxes.items() if b is not None}
        if not boxes:
            return None
        if focused_session in boxes:
            return boxes[focused_session]
        return next(iter(boxes.values()))

    def find_dialog(self, settle_s: float = 0.3) -> Optional[DialogBox]:
        """Scan for an open box, retrying until `settle_s` elapses (webview is stale post-render).

        A box short-circuits the retry; only the None case spends the full budget — which doubles
        as the "require None to persist" anti-flicker before a RESOLVE (§6, loophole #5) and the
        Path-B settle window after a raise (§4, callers pass settle_s≈1.5). Returns None only if no
        box was seen across the whole window.
        """
        deadline = settle_s
        waited = 0.0
        while True:
            box = self._scan_dialogs()
            if box is not None:
                return box
            if waited >= deadline:
                return None
            time.sleep(0.05)
            waited += 0.05

    def _dialog_signature(self, box: Optional[DialogBox]):
        """Hashable identity of a box for the observer's debounce (ignore no-op refires)."""
        if box is None:
            return None
        return (box.type, box.session, box.options, box.submit, box.submit_enabled, box.selected)

    def observe_dialog(self, on_change: Callable[[Optional[DialogBox]], None]) -> bool:
        """App-root AXObserver on AXFocusedUIElementChanged (§2): both box types fire it on
        appear/resolve. Signature-debounced so an unrelated focus change doesn't re-announce.
        Attach ONLY while VS Code is foreground (the caller enforces via AppState).
        """
        el, pid = _app_element(self.bundle_id)
        if el is None or pid is None:
            log.error("observe_dialog: %s not running (pid=%s)", self.bundle_id, pid)
            return False
        self._dialog_cb = on_change
        self._last_dialog_sig = _UNSCANNED

        @objc.callbackFor(AXObserverCreate)
        def handler(observer, element, notification, refcon):
            # Fires on every focus change (frequent). Do ONE scan in the common path — cheap. Only
            # when it's a box→None edge (a potential RESOLVE, which a webview flicker could fake) do
            # we confirm with a single short re-scan before believing it (anti-flicker, loophole #5).
            box = self._scan_dialogs()
            if box is None and self._last_dialog_sig not in (None, _UNSCANNED):
                time.sleep(0.08)
                box = self._scan_dialogs()
            sig = self._dialog_signature(box)
            if sig != self._last_dialog_sig:
                self._last_dialog_sig = sig
                log.debug("dialog observer: change → %r", box)
                if self._dialog_cb is not None:
                    self._dialog_cb(box)

        return self._dialog_obs.attach(pid, el, "AXFocusedUIElementChanged", handler)

    def stop_observing_dialog(self) -> None:
        self._dialog_cb = None
        self._last_dialog_sig = _UNSCANNED
        self._dialog_obs.detach()
