"""Golden table for command match + strip (ARCHITECTURE §7.3) — the riskiest logic."""
import pytest

from hey_claude.commands import Commands

WORDS = {
    "send": ["send", "submit", "go", "enter"],
    "cancel": ["cancel", "scratch that", "nevermind", "clear", "clear all"],
    "stop": ["stop", "interrupt", "abort"],
}
PLACEHOLDERS = ["Queue another message…"]


def C(prefix):
    return Commands(WORDS, prefixes=prefix, placeholders=PLACEHOLDERS)


@pytest.mark.parametrize(
    "text,prefix,action,strip,post",
    [
        # prefix REQUIRED mode
        ("add retry loop okay send", ["okay"], "send", 10, "add retry loop"),
        ("add retry loop send", ["okay"], None, None, None),          # no prefix ⇒ not a command
        ("remind me to send the invoice", ["okay"], None, None, None),
        ("fix the bug okay cancel", ["okay"], "cancel", None, "fix the bug"),
        ("draft text okay clear", ["okay"], "cancel", None, "draft text"),          # "clear" wipes the box
        ("draft text okay clear all", ["okay"], "cancel", None, "draft text"),       # multi-word "clear all"
        ("okay stop", ["okay"], "stop", None, ""),
        # dictation renders "okay send" as "Okay. Send." — caps + sentence punctuation
        # (found on the first live ride, 2026-07-06)
        ("add retry loop Okay. Send.", ["okay"], "send", 12, "add retry loop"),
        ("Okay. Send.", ["okay"], "send", 11, ""),
        ("do it Okay, send", ["okay"], "send", None, "do it"),
        ("hello there okay send", ["okay"], "send", None, "hello there"),
        # BARE mode
        ("add retry loop send", [], "send", 5, "add retry loop"),
        ("remind me to send the invoice", [], None, None, None),
        ("Send.", [], "send", 5, ""),                                  # case + trailing punct
        ("write tests. Send.", [], "send", None, "write tests."),       # bare + dictation punctuation
        ("please resend", [], None, None, None),                       # word boundary: "resend" != "send"
        # empties / placeholder
        ("Queue another message…", [], None, None, None),
        ("", [], None, None, None),
        ("   ", [], None, None, None),
    ],
)
def test_match_and_strip(text, prefix, action, strip, post):
    m = C(prefix).match(text)
    assert (m.action if m else None) == action
    if action is not None:
        assert m.post_text == post
        if strip is not None:
            assert m.strip_len == strip


def test_longest_match_wins_prefix_recorded():
    m = C(["okay"]).match("do the thing okay submit")
    assert m.action == "send" and m.prefix == "okay"
    assert m.post_text == "do the thing"
