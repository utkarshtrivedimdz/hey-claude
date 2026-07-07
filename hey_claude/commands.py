"""Command detection + strip (Option A, pure logic).

The extension's dictation transcribes the command word INTO the box, so we detect a
trailing command word (optionally requiring a disambiguating prefix, Q11b) and
compute how many characters to backspace so the command never reaches Claude.

Matching is TOKEN-based: real dictation renders "okay send" as "Okay. Send." (caps
+ sentence punctuation), so we normalize each token (strip edge punctuation,
lowercase) rather than matching raw whitespace. This gap was found on the first live
ride (2026-07-06) — see tests/test_commands.py for the dictation cases.

No I/O here — the most-tested module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

_TOKEN = re.compile(r"\S+")
_EDGE_PUNCT = ".,!?;:\"')(“”‘’…"


@dataclass
class Match:
    action: str        # "send" | "cancel" | "stop" | "press"
    phrase: str        # matched trailing substring in the original text (incl. leading ws)
    strip_len: int     # chars to backspace from the end to remove `phrase`
    post_text: str     # box text after strip (rstripped) — what actually gets sent
    prefix: Optional[str] = None  # the disambiguation prefix used, if any
    target: Optional[str] = None  # for "press": the spoken label to match against a control


class Commands:
    def __init__(
        self,
        words: Dict[str, List[str]],
        prefixes: Optional[List[str]] = None,
        placeholders: Optional[List[str]] = None,
        press_verbs: Optional[List[str]] = None,
    ):
        # each synonym → list of lowercased tokens (supports multi-word, e.g. "scratch that");
        # longest synonyms first so they win over any shorter sub-match.
        self.words = {
            a: sorted((s.lower().split() for s in syns), key=len, reverse=True)
            for a, syns in words.items()
        }
        self.prefixes = [p.lower() for p in (prefixes or []) if p]
        self.placeholders = set(placeholders or [])
        self.press_verbs = {v.lower() for v in (press_verbs or ["press"]) if v}

    def match(self, text: Optional[str]) -> Optional[Match]:
        """Return the trailing command in `text`, or None. Longest match wins."""
        if not text:
            return None
        stripped = text.strip()
        if not stripped or stripped in self.placeholders:
            return None

        toks = [(m.start(), m.group()) for m in _TOKEN.finditer(text)]
        norms = [raw.strip(_EDGE_PUNCT).lower() for _, raw in toks]
        if not norms:
            return None

        best: Optional[Match] = None
        for action, syn_lists in self.words.items():
            for syn in syn_lists:
                n = len(syn)
                if n == 0 or len(norms) < n or norms[-n:] != syn:
                    continue
                cmd_i = len(toks) - n
                if self.prefixes:
                    if cmd_i == 0 or norms[cmd_i - 1] not in self.prefixes:
                        continue
                    start_i, prefix_used = cmd_i - 1, norms[cmd_i - 1]
                else:
                    start_i, prefix_used = cmd_i, None

                start = toks[start_i][0]
                while start > 0 and text[start - 1].isspace():  # swallow leading whitespace
                    start -= 1
                cand = Match(
                    action=action,
                    phrase=text[start:],
                    strip_len=len(text) - start,
                    post_text=text[:start].rstrip(),
                    prefix=prefix_used,
                )
                if best is None or cand.strip_len > best.strip_len:
                    best = cand
        return best

    def match_press(self, text: Optional[str]) -> Optional[Match]:
        """Match `<prefix> press <label>` — the label is everything spoken after the verb.

        Unlike `match` (trailing fixed words), press takes an argument: the target label
        follows the verb. We scan from the end for the last press verb that satisfies the
        prefix requirement, then take the normalized tokens after it as the target. Returns
        None if there's no verb, no valid prefix, or an empty label ("okay press" alone).
        """
        if not text:
            return None
        stripped = text.strip()
        if not stripped or stripped in self.placeholders:
            return None
        toks = [(m.start(), m.group()) for m in _TOKEN.finditer(text)]
        norms = [raw.strip(_EDGE_PUNCT).lower() for _, raw in toks]
        if not norms:
            return None

        for vi in range(len(norms) - 1, -1, -1):
            if norms[vi] not in self.press_verbs:
                continue
            if self.prefixes:
                if vi == 0 or norms[vi - 1] not in self.prefixes:
                    continue
                start_i, prefix_used = vi - 1, norms[vi - 1]
            else:
                start_i, prefix_used = vi, None

            target = " ".join(n for n in norms[vi + 1:] if n)
            if not target:
                continue  # "okay press" with no label — not a press command

            start = toks[start_i][0]
            while start > 0 and text[start - 1].isspace():  # swallow leading whitespace
                start -= 1
            return Match(
                action="press",
                phrase=text[start:],
                strip_len=len(text) - start,
                post_text=text[:start].rstrip(),
                prefix=prefix_used,
                target=target,
            )
        return None


def from_config(cfg) -> Commands:
    return Commands(
        cfg.command_words, cfg.command_prefix, cfg.placeholders,
        press_verbs=getattr(cfg, "press_verbs", None),
    )


class Fixups:
    """Case-insensitive, whole-word correction of dictation mishearings (FR-5).

    Dictation mishears proper nouns ("Claude" → "clot code"). We map mishearing →
    correction and apply it to the prompt AFTER the trailing command is stripped and
    BEFORE Return, via the verified read→Cmd+A→retype path. Longest keys first so a
    multi-word mishearing ("clod code" → "Claude Code") wins over a single-word one.

    Replacements go through a function callback (not re.sub's string form) so a
    correction containing backslashes or "\\g" can never be misread as a group ref.
    """

    def __init__(self, mapping: Optional[Dict[str, str]] = None):
        pairs = sorted(
            ((k.strip(), v) for k, v in (mapping or {}).items() if k and k.strip()),
            key=lambda kv: len(kv[0].split()), reverse=True,
        )
        self._subs = [
            (re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE), v) for k, v in pairs
        ]

    def apply(self, text: Optional[str]) -> Optional[str]:
        if not text or not self._subs:
            return text
        out = text
        for pat, repl in self._subs:
            out = pat.sub(lambda _m, r=repl: r, out)
        return out


def fixups_from_config(cfg) -> Fixups:
    return Fixups(getattr(cfg, "fixups", None))
