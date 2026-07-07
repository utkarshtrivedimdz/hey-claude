"""FR-6 state machine — transitions, strip ops, self-trigger, disarm, corrections."""
import pytest

from hey_claude.config import Config
from hey_claude.commands import from_config, Fixups
from hey_claude.state import StateMachine, S, LEGAL, IllegalTransition
from hey_claude.bootstrap import BootstrapResult
from tests.fakes import FakeClock, FakeKeys, FakeAX, FakeBootstrap, FakeTelemetry


def make(boot=None, fixups=None, ax=None, beeps=None):
    cfg = Config()
    cfg.command_prefix = ["okay"]
    clock = FakeClock()
    keys, tel = FakeKeys(), FakeTelemetry()
    ax = ax if ax is not None else FakeAX()
    boot = boot or FakeBootstrap()
    beep = (lambda k: beeps.append(k)) if beeps is not None else (lambda k: None)
    # strict=True so an illegal transition is a hard failure in tests (prod only logs).
    sm = StateMachine(cfg, boot, keys, ax, from_config(cfg), tel,
                      monotonic=clock.now, fixups=fixups, strict=True, beep=beep)
    return sm, cfg, clock, keys, ax, tel, boot


def test_happy_send_strips_command_and_submits():
    sm, cfg, clock, keys, ax, tel, boot = make()
    sm.on_wake(0.9)
    assert sm.state is S.DICTATING
    assert keys.names() == ["reveal_claude", "cmd_esc"]  # reveal Claude editor, focus box; dictation is AXPress
    assert "press_dictation" in ax.ops               # started dictation via the button
    assert ax.box_observing

    ax.feed("hi okay send")                           # dictation transcribes prompt+cmd
    assert sm.state is S.IDLE
    # stop dictation, strip " okay send" (10), submit
    assert ("backspace", 10) in keys.ops
    assert keys.names()[-2:] == ["backspace", "ret"]
    assert not ax.box_observing
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


def test_no_silence_timeout_keeps_dictating():
    # The button is the sole authority: silence does NOT disarm — a turn ends only on a
    # command word or a button-off event. So ticking far past any old timeout is a no-op.
    sm, cfg, clock, keys, ax, tel, boot = make()
    sm.on_wake(0.9)
    assert sm.state is S.DICTATING
    clock.advance(600.0)
    sm.tick()
    assert sm.state is S.DICTATING                     # still dictating; no silence disarm
    assert not any(o == "press_dictation" for o in ax.ops[1:])  # button not toggled off


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


def test_external_mic_off_disarms_with_dropped_beep():
    # Button is ground truth: if it goes off mid-turn (user clicks mic), hey-claude disarms.
    beeps: list = []
    sm, cfg, clock, keys, ax, tel, boot = make(beeps=beeps)
    sm.on_wake(0.9)
    assert sm.state is S.DICTATING
    ax.feed_dictation(False)                          # external toggle off
    assert sm.state is S.IDLE
    assert "ret" not in keys.names()                  # never auto-sends
    assert beeps[-1] == "dropped"                     # audible external-drop feedback (option b)
    assert tel.turns[-1]["outcome"] == "dictation_dropped"


def test_dictation_button_missing_waits_then_aborts_loud():
    # View not up yet (button absent) → stay ARMED awaiting the tab-open event (no blind abort,
    # no re-reveal storm). When the view opens but the button is genuinely missing → abort loud.
    ax = FakeAX(button_present=False)
    sm, cfg, clock, keys, _ax, tel, boot = make(ax=ax)
    sm.on_wake(0.9)
    assert sm.state is S.ARMED                         # waiting for the view, not aborted
    assert keys.names() == ["reveal_claude"]           # exactly ONE reveal, no cmd_esc yet
    assert ax.ready_observing
    ax.feed_ready()                                    # view opened, button still absent
    assert sm.state is S.IDLE
    assert not ax.box_observing
    assert "ret" not in keys.names()                  # never sends
    assert tel.turns[-1]["outcome"] == "error"


def test_wake_storm_while_awaiting_ready_fires_single_reveal():
    # The bug: from a Markdown-preview tab, continuous speech produced many wakes and each fired
    # claude-vscode.editor.open → a pile of Claude tabs. Now a wake that can't reach dictation
    # stays ARMED awaiting the tab-open event, so the self-trigger guard swallows every
    # subsequent wake — one reveal, not N.
    ax = FakeAX(button_present=False)
    sm, cfg, clock, keys, _ax, tel, boot = make(ax=ax)
    sm.on_wake(0.9)
    assert sm.state is S.ARMED
    for _ in range(7):
        sm.on_wake(0.9)                                # continuous-speech wake flood
    assert sm.state is S.ARMED
    assert boot.calls == 1                             # armed once
    assert keys.names().count("reveal_claude") == 1    # ONE editor.open, not 8


def test_claude_view_opens_after_wait_then_dictates():
    # Cold Claude view: button absent at first (ARMED, awaiting event), appears when the tab
    # opens → the ready event drives focus + dictation without a second reveal.
    ax = FakeAX(button_present=False)
    sm, cfg, clock, keys, _ax, tel, boot = make(ax=ax)
    sm.on_wake(0.9)
    assert sm.state is S.ARMED
    ax.button_present = True                           # tab finished opening
    ax.feed_ready()
    assert sm.state is S.DICTATING
    assert keys.names() == ["reveal_claude", "cmd_esc"]  # single reveal, then focus
    assert "press_dictation" in ax.ops
    assert ax.box_observing


def test_dictation_already_on_enters_dictating_without_pressing():
    sm, cfg, clock, keys, ax, tel, boot = make(ax=FakeAX(dict_on=True))
    sm.on_wake(0.9)
    assert sm.state is S.DICTATING
    assert "press_dictation" not in ax.ops            # already recording → no toggle
    assert ax.box_observing


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


# ---- Phase 1: transition chokepoint --------------------------------------

def test_legal_table_is_exhaustive():
    # Every state must be a key in LEGAL (no state can transition without a rule).
    assert set(LEGAL.keys()) == set(S)
    # Targets are real states, and no state declares an unreachable self-loop we forgot.
    for src, dsts in LEGAL.items():
        assert dsts <= set(S)


def test_illegal_transition_raises_in_strict_mode():
    sm, *_ = make()                      # starts IDLE
    with pytest.raises(IllegalTransition):
        sm._transition(S.DICTATING, "bogus")   # IDLE → DICTATING is not declared


def test_illegal_transition_logs_but_does_not_raise_when_not_strict():
    # Prod construction (strict defaults False): illegal is logged, not fatal.
    cfg = Config()
    clock, keys, ax, tel = FakeClock(), FakeKeys(), FakeAX(), FakeTelemetry()
    sm = StateMachine(cfg, FakeBootstrap(), keys, ax, from_config(cfg), tel,
                      monotonic=clock.now)
    sm._transition(S.DICTATING, "bogus")       # no raise
    assert sm.state is S.DICTATING
    assert tel.transitions[-1]["illegal"] is True


def test_happy_send_emits_legal_transition_sequence():
    sm, cfg, clock, keys, ax, tel, boot = make()
    sm.on_wake(0.9)
    ax.feed("hi okay send")
    assert sm.state is S.IDLE
    reasons = [(t["frm"], t["to"], t["reason"]) for t in tel.transitions]
    assert reasons == [
        ("idle", "armed", "wake_accepted"),
        ("armed", "dictating", "dictation_started"),
        ("dictating", "acting", "match_send"),   # Phase 2: ACTING extracts dispatch
        ("acting", "idle", "dispatched_sent"),
    ]
    assert all(t["illegal"] is False for t in tel.transitions)


def test_arm_timeout_disarms_when_button_never_turns_on():
    # Button press produced no on-event → the ARMED backstop disarms (the only tick timeout left).
    ax = FakeAX()
    ax.press_dictation = lambda: ax.ops.append("press_dictation")  # press, but fire no event
    sm, cfg, clock, keys, _ax, tel, boot = make(ax=ax)
    sm.on_wake(0.9)
    assert sm.state is S.ARMED                          # stuck armed: no button-on event arrived
    clock.advance(cfg.arm_timeout_s + 1.0)
    sm.tick()
    assert sm.state is S.IDLE
    assert tel.transitions[-1]["reason"] == "arm_timeout"


def test_bootstrap_failure_emits_armed_then_idle():
    boot = FakeBootstrap(BootstrapResult(ok=False, cold_start=False, ms=9, focus_gate="abort"))
    sm, cfg, clock, keys, ax, tel, _ = make(boot=boot)
    sm.on_wake(0.9)
    reasons = [(t["frm"], t["to"], t["reason"]) for t in tel.transitions]
    assert reasons == [
        ("idle", "armed", "wake_accepted"),
        ("armed", "idle", "bootstrap_failed"),
    ]
