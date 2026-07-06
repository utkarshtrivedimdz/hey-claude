"""Golden table for command match + strip (ARCHITECTURE §7.3) — the riskiest logic."""
import pytest

from chotu.commands import Commands

WORDS = {
    "send": ["send", "submit", "go", "enter"],
    "cancel": ["cancel", "scratch that", "nevermind"],
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
        ("okay stop", ["okay"], "stop", None, ""),
        # BARE mode
        ("add retry loop send", [], "send", 5, "add retry loop"),
        ("remind me to send the invoice", [], None, None, None),
        ("Send.", [], "send", 5, ""),                                  # case + trailing punct
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
    if action == "send":
        assert m.strip_len == strip
        assert m.post_text == post
    elif action is not None:
        assert m.post_text == post


def test_longest_match_wins_prefix_recorded():
    m = C(["okay"]).match("do the thing okay submit")
    assert m.action == "send" and m.prefix == "okay"
    assert m.post_text == "do the thing"
