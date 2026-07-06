"""Does the 'Voice dictation' button emit an AX notification when it toggles?

Attaches an AXObserver to the button, registers a broad set of candidate
notifications, then AXPresses it ON (t≈1s) and OFF (t≈3s) while pumping the run
loop, logging every notification received with the button's AXDescription at that
moment. Determines whether an EVENT-BASED dictation-state design is feasible
(vs. polling). Run with VS Code frontmost, Claude panel open.

    .venv/bin/python -m scripts.ax_notify_probe
"""
from __future__ import annotations

import time, sys
import objc
from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue, AXUIElementPerformAction,
    AXObserverCreate, AXObserverAddNotification, AXObserverGetRunLoopSource,
)
from CoreFoundation import (
    CFRunLoopGetCurrent, CFRunLoopAddSource, CFRunLoopRunInMode,
    kCFRunLoopDefaultMode,
)

BUNDLE = "com.microsoft.VSCode"

# Candidate notifications to listen for (superset; most won't fire).
NOTIFS = [
    "AXValueChanged", "AXTitleChanged", "AXUIElementDestroyed",
    "AXLayoutChanged", "AXSelectedChildrenChanged", "AXLiveRegionChanged",
    "AXLiveRegionCreated", "AXExpandedChanged", "AXMenuOpened", "AXMenuClosed",
    "AXFocusedUIElementChanged", "AXSelectedTextChanged", "AXRowCountChanged",
    "AXAnnouncementRequested", "AXLayoutComplete",
]


def app_pid_el():
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        if a.bundleIdentifier() == BUNDLE:
            return a.processIdentifier(), AXUIElementCreateApplication(a.processIdentifier())
    return None, None


def get(el, attr):
    err, val = AXUIElementCopyAttributeValue(el, attr, None)
    return val if err == 0 else None


def find_button(el, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > 20000:
        return None
    if get(el, "AXRole") == "AXButton" and get(el, "AXDescription") in ("Voice dictation", "Stop recording"):
        return el
    for c in (get(el, "AXChildren") or []):
        r = find_button(c, count)
        if r is not None:
            return r
    return None


def main():
    pid, root = app_pid_el()
    if root is None:
        print("VS Code not running."); return 1
    AXUIElementSetAttributeValue(root, "AXManualAccessibility", True)
    btn = find_button(root)
    if btn is None:
        print("button not found (Claude panel open? VS Code frontmost?)"); return 1
    print(f"button found, AXDescription={get(btn,'AXDescription')!r}, pid={pid}")

    fired = []

    @objc.callbackFor(AXObserverCreate)
    def handler(observer, element, notification, refcon):
        desc = get(element, "AXDescription")
        t = time.monotonic()
        fired.append((notification, desc))
        print(f"  [{t:.2f}] NOTIF {notification!r}  AXDescription={desc!r}")

    err, observer = AXObserverCreate(pid, handler, None)
    if err != 0 or observer is None:
        print(f"AXObserverCreate failed err={err}"); return 1

    registered = []
    for n in NOTIFS:
        rc = AXObserverAddNotification(observer, btn, n, None)
        if rc == 0:
            registered.append(n)
    print(f"registered notifications: {registered}\n")

    CFRunLoopAddSource(CFRunLoopGetCurrent(), AXObserverGetRunLoopSource(observer), kCFRunLoopDefaultMode)

    t0 = time.monotonic()
    pressed_on = pressed_off = False
    while time.monotonic() - t0 < 6.0:
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.1, False)
        dt = time.monotonic() - t0
        if not pressed_on and dt > 1.0:
            print(">>> AXPress → ON"); AXUIElementPerformAction(btn, "AXPress"); pressed_on = True
        if not pressed_off and dt > 3.0:
            print(">>> AXPress → OFF"); AXUIElementPerformAction(btn, "AXPress"); pressed_off = True

    print(f"\n=== notifications that fired: {sorted(set(n for n,_ in fired))} ===")
    if not fired:
        print("NONE fired on the button element — event-based on the button directly is NOT viable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
