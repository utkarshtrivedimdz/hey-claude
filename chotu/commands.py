"""Command detection + strip (Option A, pure logic).

The extension's dictation transcribes the command word INTO the box, so we detect a
trailing command word (optionally requiring a disambiguating prefix, Q11b) and
compute how many characters to backspace so the command never reaches Claude.

No I/O here — this is the most-tested module (see tests/test_commands.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class Match:
    action: str        # "send" | "cancel" | "stop"
    phrase: str        # matched trailing substring in the original text (incl. leading ws)
    strip_len: int     # chars to backspace from the end to remove `phrase`
    post_text: str     # box text after strip (rstripped) — what actually gets sent
    prefix: Optional[str] = None  # the disambiguation prefix used, if any


class Commands:
    def __init__(
        self,
        words: Dict[str, List[str]],
        prefixes: Optional[List[str]] = None,
        placeholders: Optional[List[str]] = None,
    ):
        self.words = {a: sorted(syns, key=len, reverse=True) for a, syns in words.items()}
        self.prefixes = [p for p in (prefixes or []) if p]
        self.placeholders = set(placeholders or [])
        self._patterns: Dict[str, re.Pattern] = {}

        # A leading (?:^|\s+) both provides a word boundary (so "resend" != "send")
        # and consumes the whitespace before the command so strip removes it too.
        pfx = ""
        if self.prefixes:
            pfx = r"(?:%s)\s+" % "|".join(re.escape(p) for p in self.prefixes)
        for action, syns in self.words.items():
            syn = "|".join(re.escape(s) for s in syns)
            self._patterns[action] = re.compile(
                r"(?:^|\s+)%s(?:%s)\s*[.!?,;:]*\s*$" % (pfx, syn), re.IGNORECASE
            )

    def match(self, text: Optional[str]) -> Optional[Match]:
        """Return the trailing command in `text`, or None. Longest match wins."""
        if not text:
            return None
        stripped = text.strip()
        if not stripped or stripped in self.placeholders:
            return None

        best: Optional[Match] = None
        for action, pat in self._patterns.items():
            m = pat.search(text)
            if not m:
                continue
            start = m.start()
            strip_len = len(text) - start
            cand = Match(
                action=action,
                phrase=text[start:],
                strip_len=strip_len,
                post_text=text[:start].rstrip(),
                prefix=(self._extract_prefix(text[start:]) if self.prefixes else None),
            )
            if best is None or cand.strip_len > best.strip_len:
                best = cand
        return best

    def _extract_prefix(self, phrase: str) -> Optional[str]:
        toks = phrase.strip().split()
        if toks:
            low = toks[0].lower()
            for p in self.prefixes:
                if p.lower() == low:
                    return p
        return None


def from_config(cfg) -> Commands:
    return Commands(cfg.command_words, cfg.command_prefix, cfg.placeholders)
