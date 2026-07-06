"""Phase 2 — Actions.perform keystroke/AX choreography, isolated from the state machine.

Sequencing (ACTING transition, telemetry, correction window) is tested in test_state.py;
here we assert only *how* each command is executed over fake keys/ax ports.
"""
from hey_claude.actions import Actions, ActionOutcome
from hey_claude.commands import from_config, Fixups
from hey_claude.config import Config
from tests.fakes import FakeKeys, FakeAX


def _cmds(**over):
    cfg = Config()
    cfg.command_prefix = ["okay"]
    for k, v in over.items():
        setattr(cfg, k, v)
    return from_config(cfg)


def _match(text, **over):
    m = _cmds(**over).match(text)
    assert m is not None, f"expected a command match in {text!r}"
    return m


def make(fixups=None, dict_on=False):
    keys, ax = FakeKeys(), FakeAX(dict_on=dict_on)
    return Actions(keys, ax, fixups), keys, ax


def test_perform_acts_live_then_frees_mic():
    # Act on the live dictated text first, THEN turn the mic off (text only exists in the
    # box while the mic is on). So dictation is still on during the keystrokes, off after.
    actions, keys, ax = make(dict_on=True)
    ax.box_observing = True
    out = actions.perform(_match("hi okay send"), "hi okay send")
    assert not ax.box_observing             # released the box observer
    assert "press_dictation" in ax.ops      # mic freed…
    assert not ax.dictation_on()            # …so the button ends up off
    assert isinstance(out, ActionOutcome)


def test_send_fast_path_backspaces_and_returns():
    actions, keys, ax = make()
    out = actions.perform(_match("hi okay send"), "hi okay send")
    assert keys.names() == ["backspace", "ret"]
    assert ("backspace", 10) in keys.ops    # strips " okay send"
    assert out == ActionOutcome("sent", "send", strip=10, box_post="hi")


def test_send_with_fixup_rewrites_whole_box():
    actions, keys, ax = make(Fixups({"clot code": "Claude Code"}))
    out = actions.perform(_match("open clot code okay send"), "open clot code okay send")
    assert keys.names()[-3:] == ["clear", "type", "ret"]   # select-all, retype, submit
    assert ("type", "open Claude Code") in keys.ops
    assert not any(o[0] == "backspace" for o in keys.ops)  # rewrite path, not fast path
    assert out.outcome == "sent"
    assert out.box_post == "open Claude Code"


def test_send_without_matching_fixup_uses_fast_path():
    actions, keys, ax = make(Fixups({"clot code": "Claude Code"}))
    out = actions.perform(_match("nothing to fix okay send"), "nothing to fix okay send")
    assert keys.names()[-2:] == ["backspace", "ret"]
    assert not any(o[0] == "type" for o in keys.ops)
    assert out.box_post == "nothing to fix"


def test_empty_box_send_is_noop_abort():
    actions, keys, ax = make()
    out = actions.perform(_match("okay send"), "okay send")   # box is only the command
    assert keys.names() == []                # no send keystrokes (dictation was already off)
    assert not any(o[0] in ("backspace", "ret", "type", "clear") for o in keys.ops)
    assert out == ActionOutcome("empty", "error", strip=0, box_post="")


def test_cancel_clears_box_and_does_not_return():
    actions, keys, ax = make()
    out = actions.perform(_match("bad prompt okay cancel"), "bad prompt okay cancel")
    assert "clear" in keys.names()
    assert "ret" not in keys.names()
    assert out.outcome == "cancelled" and out.beep == "cancel"


def test_stop_sends_escape():
    actions, keys, ax = make()
    out = actions.perform(_match("okay stop"), "okay stop")
    assert keys.names() == ["esc"]
    assert out.outcome == "stopped" and out.beep == "stop"


def test_default_fixups_is_noop():
    actions, keys, ax = make()   # no fixups injected
    out = actions.perform(_match("open clot code okay send"), "open clot code okay send")
    assert keys.names()[-2:] == ["backspace", "ret"]
    assert out.box_post == "open clot code"
