"""Keystroke injection (KeysPort impl) via osascript System Events.

Verified 2026-07-06: type + backspace + Return land in the box (AX writes don't
persist, so keystrokes are the write path). Cmd+D=key 2, Esc/Cmd+Esc=key 53,
Return=key 36, Delete/backspace=key 51.
"""
from __future__ import annotations

import subprocess


def _osa(script: str) -> None:
    subprocess.run(["osascript", "-e", script], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class RealKeys:
    def __init__(self, keymap: dict):
        self.k = keymap

    def _key(self, code: int, cmd: bool = False) -> None:
        mod = " using command down" if cmd else ""
        _osa(f'tell application "System Events" to key code {code}{mod}')

    def cmd_d(self) -> None:
        self._key(self.k["cmd_d"], cmd=True)

    def cmd_esc(self) -> None:
        self._key(self.k["cmd_esc"], cmd=True)

    def esc(self) -> None:
        self._key(self.k["esc"])

    def ret(self) -> None:
        self._key(self.k["ret"])

    def backspace(self, n: int) -> None:
        if n <= 0:
            return
        code = self.k["backspace"]
        # one osascript call for the whole run — much faster than n subprocesses
        _osa(
            'tell application "System Events"\n'
            f'repeat {int(n)} times\nkey code {code}\nend repeat\n'
            'end tell'
        )

    def clear(self) -> None:
        self._key(self.k["a"], cmd=True)   # Cmd+A (select all)
        self._key(self.k["backspace"])     # delete

    def type_text(self, s: str) -> None:
        if not s:
            return
        esc = s.replace("\\", "\\\\").replace('"', '\\"')
        _osa(f'tell application "System Events" to keystroke "{esc}"')
