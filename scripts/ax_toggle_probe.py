"""Find the 'Voice dictation' button, snapshot ALL its AX attributes OFF → press →
ON → press → OFF, to learn which attribute encodes the blue/on state. Also proves
AXPress actually toggles dictation. Run with VS Code frontmost, Claude panel open.
"""
from __future__ import annotations

import time, sys
from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue, AXUIElementCopyAttributeNames,
    AXUIElementPerformAction,
)

BUNDLE = "com.microsoft.VSCode"


def app_el():
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        if a.bundleIdentifier() == BUNDLE:
            return AXUIElementCreateApplication(a.processIdentifier())
    return None


def get(el, attr):
    err, val = AXUIElementCopyAttributeValue(el, attr, None)
    return val if err == 0 else None


def find_button(el, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > 20000:
        return None
    if (get(el, "AXRole") == "AXButton" and get(el, "AXDescription") == "Voice dictation"):
        return el
    for c in (get(el, "AXChildren") or []):
        r = find_button(c, count)
        if r is not None:
            return r
    return None


def snapshot(el):
    err, names = AXUIElementCopyAttributeNames(el, None)
    names = list(names) if err == 0 and names else []
    return {n: get(el, n) for n in names}


def show(label, snap):
    print(f"\n=== {label} ===")
    for k in sorted(snap):
        v = snap[k]
        if k in ("AXChildren", "AXParent", "AXTopLevelUIElement", "AXWindow"):
            continue
        print(f"    {k}: {v!r}")


def main():
    root = app_el()
    if root is None:
        print("VS Code not running."); return 1
    AXUIElementSetAttributeValue(root, "AXManualAccessibility", True)
    btn = find_button(root)
    if btn is None:
        print("Voice dictation button not found (Claude panel open? VS Code frontmost?)"); return 1

    off1 = snapshot(btn)
    show("OFF (before)", off1)

    print("\n>>> AXPress (turn dictation ON) ...")
    AXUIElementPerformAction(btn, "AXPress")
    time.sleep(1.5)
    on = snapshot(btn)
    show("ON (after press)", on)

    print("\n>>> AXPress again (turn dictation OFF) ...")
    AXUIElementPerformAction(btn, "AXPress")
    time.sleep(1.5)
    off2 = snapshot(btn)
    show("OFF (after 2nd press)", off2)

    changed = [k for k in on if k not in ("AXChildren", "AXParent") and off1.get(k) != on.get(k)]
    print("\n=== ATTRIBUTES THAT CHANGED OFF→ON ===")
    for k in changed:
        print(f"    {k}: {off1.get(k)!r}  ->  {on.get(k)!r}")
    if not changed:
        print("    (none — state may not be exposed via a changed attribute)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
