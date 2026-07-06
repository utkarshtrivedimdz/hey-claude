"""One-shot AX probe: find the 'Voice dictation' button in VS Code and report its
state + available actions. Diagnostic for the Cmd+D → AXPress dictation fix and the
"is dictation actually on?" feedback. Run with VS Code frontmost, Claude panel open.

    .venv/bin/python -m scripts.ax_probe
"""
from __future__ import annotations

import sys
from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue, AXUIElementCopyActionNames,
)

BUNDLE = "com.microsoft.VSCode"
KEYWORDS = ("voice", "dictation", "mic", "speech", "record")
ATTRS = ("AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue",
         "AXHelp", "AXIdentifier", "AXRoleDescription", "AXSelected")


def app_el():
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        if a.bundleIdentifier() == BUNDLE:
            return AXUIElementCreateApplication(a.processIdentifier())
    return None


def get(el, attr):
    err, val = AXUIElementCopyAttributeValue(el, attr, None)
    return val if err == 0 else None


def actions(el):
    err, names = AXUIElementCopyActionNames(el, None)
    return list(names) if err == 0 and names else []


def describe(el):
    d = {a: get(el, a) for a in ATTRS}
    d = {k: v for k, v in d.items() if v is not None and v != ""}
    d["_actions"] = actions(el)
    return d


def walk(el, depth=0, max_depth=40, hits=None, count=None):
    if hits is None:
        hits = []
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > 20000 or depth > max_depth:
        return hits
    role = get(el, "AXRole") or ""
    title = get(el, "AXTitle") or ""
    desc = get(el, "AXDescription") or ""
    help_ = get(el, "AXHelp") or ""
    ident = get(el, "AXIdentifier") or ""
    blob = " ".join((title, desc, help_, ident)).lower()
    if any(k in blob for k in KEYWORDS):
        hits.append(describe(el))
    children = get(el, "AXChildren") or []
    for c in children:
        walk(c, depth + 1, max_depth, hits, count)
    return hits


def main():
    el = app_el()
    if el is None:
        print("VS Code not running.")
        return 1
    AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    print(f"frontmost = {front.bundleIdentifier() if front else None}  (want {BUNDLE})")
    hits = walk(el)
    print(f"\nelements matching {KEYWORDS}: {len(hits)}\n")
    for i, h in enumerate(hits, 1):
        acts = h.pop("_actions", [])
        print(f"--- match #{i} ---")
        for k, v in h.items():
            print(f"    {k}: {v!r}")
        print(f"    actions: {acts}   {'<< AXPress-able' if 'AXPress' in acts else ''}")
        print()
    if not hits:
        print("No voice/dictation elements found. Is the Claude panel open and VS Code frontmost?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
