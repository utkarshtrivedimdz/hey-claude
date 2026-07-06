"""Keystroke injection (KeysPort impl) via osascript System Events.

Verified 2026-07-06: type + backspace + Return land in the box (AX writes don't
persist, so keystrokes are the write path). Esc/Cmd+Esc=key 53, Return=key 36,
Delete/backspace=key 51. (Dictation is NOT a keystroke — it's an AXPress on the
Voice-dictation button; see chotu/ax.py and DICTATION-AX-PLAN.md.)
"""
from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)


def _osa(script: str) -> None:
    r = subprocess.run(["osascript", "-e", script], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if r.returncode != 0:
        log.error("osascript failed (rc=%s): %s", r.returncode,
                  (r.stderr or b"").decode(errors="replace").strip())


class RealKeys:
    def __init__(self, keymap: dict):
        self.k = keymap

    def _key(self, code: int, cmd: bool = False) -> None:
        mod = " using command down" if cmd else ""
        _osa(f'tell application "System Events" to key code {code}{mod}')

    def cmd_esc(self) -> None:
        log.debug("key: Cmd+Esc (focus Claude input)")
        self._key(self.k["cmd_esc"], cmd=True)

    def esc(self) -> None:
        log.debug("key: Esc")
        self._key(self.k["esc"])

    def ret(self) -> None:
        log.debug("key: Return (submit)")
        self._key(self.k["ret"])

    def backspace(self, n: int) -> None:
        if n <= 0:
            return
        log.debug("key: backspace ×%d", n)
        code = self.k["backspace"]
        # one osascript call for the whole run — much faster than n subprocesses
        _osa(
            'tell application "System Events"\n'
            f'repeat {int(n)} times\nkey code {code}\nend repeat\n'
            'end tell'
        )

    def clear(self) -> None:
        log.debug("key: Cmd+A + Delete (clear box)")
        self._key(self.k["a"], cmd=True)   # Cmd+A (select all)
        self._key(self.k["backspace"])     # delete

    def type_text(self, s: str) -> None:
        if not s:
            return
        log.debug("key: type %d chars: %r", len(s), s[:80] + ("…" if len(s) > 80 else ""))
        esc = s.replace("\\", "\\\\").replace('"', '\\"')
        _osa(f'tell application "System Events" to keystroke "{esc}"')
