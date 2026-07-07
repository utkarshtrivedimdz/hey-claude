"""Menu bar status item (NSStatusItem) — click toggles wake listening on/off.

I/O module (pyobjc/AppKit), same layer as `system.py`/`ax.py`; never imported by the
pure modules. Lives on the main run loop, so the click handler fires exactly where the
state machine runs — no cross-thread hop. Requires an NSApplication event loop
(`NSApp.run()` in `__main__`); a bare CFRunLoopRun() won't dispatch button clicks.

The icon is an SF Symbol template image (auto-adapts to light/dark menu bars):
`waveform` when listening, `waveform.slash` when muted. On macOS without SF Symbols it
falls back to a text glyph.
"""
from __future__ import annotations

import logging
from typing import Callable

import objc
from AppKit import NSImage, NSStatusBar, NSVariableStatusItemLength
from Foundation import NSObject

log = logging.getLogger(__name__)

_ON_SYMBOL = "waveform"
_OFF_SYMBOL = "waveform.slash"


class _ClickTarget(NSObject):
    """Objective-C target for the status button's action (must be an NSObject)."""

    def initWithCallback_(self, cb):
        self = objc.super(_ClickTarget, self).init()
        if self is None:
            return None
        self._cb = cb
        return self

    def onClick_(self, sender):  # noqa: N802 (obj-c selector name)
        try:
            self._cb()
        except Exception:
            log.exception("menu bar toggle handler raised")


class MenuBar:
    """Owns the NSStatusItem. `attach` installs it; `set_listening` swaps the icon."""

    def __init__(self) -> None:
        self._item = None      # retain so the status item isn't GC'd (removes it from the bar)
        self._target = None    # retain the obj-c action target likewise

    def attach(self, on_toggle: Callable[[], None]) -> None:
        self._item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._target = _ClickTarget.alloc().initWithCallback_(on_toggle)
        button = self._item.button()
        button.setTarget_(self._target)
        button.setAction_("onClick:")
        self.set_listening(True)

    def set_listening(self, on: bool) -> None:
        if self._item is None:
            return
        button = self._item.button()
        name = _ON_SYMBOL if on else _OFF_SYMBOL
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            name, "hey-claude listening" if on else "hey-claude muted"
        )
        if image is not None:
            image.setTemplate_(True)  # tint follows the menu bar (light/dark) automatically
            button.setImage_(image)
            button.setTitle_("")
        else:  # SF Symbols unavailable (pre-macOS 11) — fall back to a glyph
            button.setImage_(None)
            button.setTitle_("\U0001F399" if on else "\U0001F507")
        button.setToolTip_(
            "hey-claude: listening (click to mute)" if on
            else "hey-claude: muted (click to listen)"
        )
