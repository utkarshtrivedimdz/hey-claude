"""FR-6 state machine — transitions, strip ops, self-trigger, disarm, corrections."""
import pytest

from chotu.config import Config
from chotu.commands import from_config, Fixups
from chotu.state import StateMachine, S
from chotu.bootstrap import BootstrapResult
from tests.fakes import FakeClock, FakeKeys, FakeAX, FakeBootstrap, FakeTelemetry


def make(boot=None, disarm=10.0, fixups=None):
    cfg = Config()
    cfg.command_prefix = ["okay"]
    cfg.disarm_timeout_s = disarm
    clock = FakeClock()
    keys, ax, tel = FakeKeys(), FakeAX(), FakeTelemetry()
    boot = boot or FakeBootstrap()
    sm = StateMachine(cfg, boot, keys, ax, from_config(cfg), tel,
                      monotonic=clock.now, fixups=fixups)
    return sm, cfg, clock, keys, ax, tel, boot


def test_happy_send_strips_command_and_submits():
    sm, cfg, clock, keys, ax, tel, boot = make()
    sm.on_wake(0.9)
    assert sm.state is S.DICTATING
    assert keys.names()[:2] == ["cmd_esc", "cmd_d"]   # focus then dictate
    assert ax.observing

    ax.feed("hi okay send")                           # dictation transcribes prompt+cmd
    assert sm.state is S.IDLE
    # stop dictation, strip " okay send" (10), submit
    assert ("backspace", 10) in keys.ops
    assert keys.names()[-2:] == ["backspace", "ret"]
    assert not ax.observing
    assert tel.turns[-1]["outcome"] == "sent"
    assert tel.turns[-1]["command"] == "send"
    assert tel.turns[-1]["box_post"] == "hi"


def test_self_trigger_ignored_while_dictating():
    sm, *_ , boot = make()
    sm.on_wake(0.9)
    assert boot.calls == 1
    sm.on_wake(0.9)                                   # second wake mid-dictation
    assert sm.state is S.DICTATING
    assert boot.calls == 1                            # not re-armed


def test_disarm_on_silence_never_sends():
    sm, cfg, clock, keys, ax, tel, boot = make(disarm=5.0)
    sm.on_wake(0.9)
    clock.advance(6.0)
    sm.tick()
    assert sm.state is S.IDLE
    assert "ret" not in keys.names()                  # cancel-safe: never auto-sends
    assert tel.turns[-1]["outcome"] == "timeout"


def test_cancel_clears_and_does_not_submit():
    sm, cfg, clock, keys, ax, tel, boot = make()
    sm.on_wake(0.9)
    ax.feed("bad prompt okay cancel")
    assert "clear" in keys.names()
    assert "ret" not in keys.names()
    assert tel.turns[-1]["outcome"] == "cancelled"


def test_bootstrap_failure_aborts_before_keystrokes():
    boot = FakeBootstrap(BootstrapResult(ok=False, cold_start=False, ms=9, focus_gate="abort"))
    sm, cfg, clock, keys, ax, tel, _ = make(boot=boot)
    sm.on_wake(0.9)
    assert sm.state is S.IDLE
    assert keys.ops == []                             # no keys fired into the wrong app
    assert tel.turns[-1]["outcome"] == "error"


def test_rejected_wake_below_threshold_does_nothing():
    sm, cfg, clock, keys, ax, tel, boot = make()
    sm.on_wake(0.1)
    assert sm.state is S.IDLE
    assert boot.calls == 0
    assert tel.wakes[-1]["accepted"] is False


def test_correction_flagged_when_cancel_follows_send():
    sm, cfg, clock, keys, ax, tel, boot = make()
    # turn 1: send
    sm.on_wake(0.9)
    ax.feed("do it okay send")
    sent_id = tel.turns[-1]["turn_id"]
    # turn 2: cancel shortly after
    clock.advance(2.0)
    sm.on_wake(0.9)
    ax.feed("okay cancel")
    assert tel.corrections and tel.corrections[-1]["turn_id"] == sent_id
    assert tel.corrections[-1]["signal"] == "cancel"


def test_fixup_rewrites_box_on_send():
    fx = Fixups({"clot code": "Claude Code"})
    sm, cfg, clock, keys, ax, tel, boot = make(fixups=fx)
    sm.on_wake(0.9)
    ax.feed("open clot code okay send")
    assert sm.state is S.IDLE
    # corrected → full rewrite path (not backspace): select-all, retype fixed prompt, Return
    assert keys.names()[-3:] == ["clear", "type", "ret"]
    assert ("type", "open Claude Code") in keys.ops
    assert not any(o[0] == "backspace" for o in keys.ops)
    assert tel.turns[-1]["outcome"] == "sent"
    assert tel.turns[-1]["box_post"] == "open Claude Code"


def test_no_fixup_uses_fast_backspace_send():
    fx = Fixups({"clot code": "Claude Code"})
    sm, cfg, clock, keys, ax, tel, boot = make(fixups=fx)
    sm.on_wake(0.9)
    ax.feed("nothing to fix okay send")
    # no mishearing → fast path unchanged (backspace the command, Return; no retype)
    assert keys.names()[-2:] == ["backspace", "ret"]
    assert not any(o[0] == "type" for o in keys.ops)
    assert tel.turns[-1]["box_post"] == "nothing to fix"


def test_default_fixups_is_noop():
    # StateMachine with no fixups injected still sends via the fast path.
    sm, cfg, clock, keys, ax, tel, boot = make()
    sm.on_wake(0.9)
    ax.feed("open clot code okay send")
    assert keys.names()[-2:] == ["backspace", "ret"]
