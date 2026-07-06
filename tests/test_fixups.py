"""Dictation fixups — case-insensitive, whole-word mishearing correction."""
import pytest

from chotu.commands import Fixups

M = {
    "clot code": "Claude Code",
    "clod code": "Claude Code",
    "clot": "Claude",
    "clod": "Claude",
}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("open clot code and fix it", "open Claude Code and fix it"),
        ("ask clot to refactor", "ask Claude to refactor"),
        ("Clot, please help", "Claude, please help"),          # case-insensitive + punct boundary
        ("CLOD CODE is great", "Claude Code is great"),          # upper-case
        # longest key wins: "clot code" beats "clot" (order-independent)
        ("clot code", "Claude Code"),
        # word boundary: substrings are not touched
        ("closet clothing", "closet clothing"),
        ("clots of blood", "clots of blood"),
        # multiple hits in one prompt
        ("clot then clod", "Claude then Claude"),
        # no-op when nothing matches → identical string
        ("just a normal prompt", "just a normal prompt"),
    ],
)
def test_apply(text, expected):
    assert Fixups(M).apply(text) == expected


def test_empty_mapping_is_identity():
    f = Fixups({})
    assert f.apply("clot code") == "clot code"
    assert f.apply(None) is None
    assert f.apply("") == ""


def test_none_mapping_is_identity():
    assert Fixups(None).apply("clot") == "clot"


def test_blank_keys_ignored():
    assert Fixups({"  ": "x", "clot": "Claude"}).apply("clot") == "Claude"


def test_replacement_with_backslash_is_literal():
    # function-callback substitution → "\1" in the correction is not a group ref.
    assert Fixups({"foo": r"a\1b"}).apply("foo") == r"a\1b"
