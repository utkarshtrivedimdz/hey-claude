"""Phase 1 — pure dialog classification (discriminator, numbered marker, scoping). No pyobjc."""
import pytest

from hey_claude.dialog import classify, DialogBox, NUMBERED


def btn(label, enabled=True):
    return {"role": "AXButton", "label": label, "enabled": enabled, "selected": False}


def radio(label, selected=False):
    return {"role": "AXRadioButton", "label": label, "enabled": True, "selected": selected}


# ---- approval box --------------------------------------------------------

def test_approval_numbered_buttons_no_radios():
    raw = [btn("1 Yes"), btn("2 Yes, allow cat for all projects"), btn("3 No")]
    box = classify(raw, session="hi")
    assert box == DialogBox(
        type="approval", session="hi",
        options=("1 Yes", "2 Yes, allow cat for all projects", "3 No"),
    )


def test_unnumbered_buttons_are_not_a_dialog():
    # Ordinary VS Code buttons ("Bypass permissions") are unnumbered → not Claude controls.
    assert classify([btn("Bypass permissions"), btn("Settings")]) is None


def test_answered_prompt_no_controls_is_none():
    # Loophole #9: a resolved prompt exposes no numbered buttons/radios.
    assert classify([]) is None
    assert classify([{"role": "AXStaticText", "label": "some transcript"}]) is None


# ---- choice box ----------------------------------------------------------

def test_choice_radios_plus_numbered_submit_selected_and_enabled():
    raw = [
        radio("Option A", selected=True),
        radio("Option B"),
        radio("Other"),
        btn("1 Submit answers", enabled=True),
    ]
    box = classify(raw, session="proj")
    assert box.type == "choice"
    assert box.session == "proj"
    assert box.options == ("Option A", "Option B", "Other")
    assert box.submit == "1 Submit answers"
    assert box.submit_enabled is True
    assert box.selected == "Option A"


def test_choice_no_selection_submit_disabled():
    raw = [radio("A"), radio("B"), btn("1 Submit answers", enabled=False)]
    box = classify(raw)
    assert box.type == "choice"
    assert box.selected is None
    assert box.submit_enabled is False


def test_choice_radios_win_discriminator_even_with_numbered_buttons():
    # radios present → choice, regardless of other numbered buttons.
    raw = [radio("A"), btn("1 Submit answers")]
    assert classify(raw).type == "choice"


def test_choice_before_submit_renders():
    # Radios can appear a beat before the Submit button — still a choice, submit=None.
    box = classify([radio("A"), radio("B")])
    assert box.type == "choice"
    assert box.submit is None
    assert box.submit_enabled is False


# ---- numbered marker -----------------------------------------------------

def test_numbered_regex_matches_leading_digit_space():
    assert NUMBERED.match("1 Yes")
    assert NUMBERED.match("12 Something")
    assert not NUMBERED.match("Yes")
    assert not NUMBERED.match("Bypass permissions")


def test_box_is_frozen():
    box = classify([btn("1 Yes")])
    with pytest.raises(Exception):
        box.type = "choice"
