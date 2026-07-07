"""Phase 1 — the reconciler backstop: drop an event, tick, assert the FSM snaps to truth. Pure."""
from hey_claude.appstate import AppStateMachine, Tab
from hey_claude.config import Config
from hey_claude.commands import from_config
from hey_claude.dialog import DialogBox
from hey_claude.reconcile import Reconciler
from hey_claude.state import StateMachine, S
from tests.fakes import FakeClock, FakeKeys, FakeAX, FakeBootstrap, FakeTelemetry, FakeSystem


BOX_A = DialogBox(type="approval", session="A", options=("1 Yes", "3 No"))
BOX_B = DialogBox(type="choice", session="B", options=("x", "y"), submit="1 Submit answers")
VSCODE = "com.microsoft.VSCode"


def build(front=VSCODE, dialog=None, tabs=(), fg=True):
    cfg = Config()
    ax = FakeAX(dialog=dialog)
    system = FakeSystem(front=front, tabs=tabs)
    appstate = AppStateMachine()
    if fg:
        appstate.on_activate()
    else:
        appstate.on_deactivate()
    sm = StateMachine(cfg, FakeBootstrap(), FakeKeys(), ax, from_config(cfg), FakeTelemetry(),
                      monotonic=FakeClock().now, appstate=appstate)
    rec = Reconciler(cfg, system, ax, sm, appstate)
    return rec, sm, ax, system, appstate


def test_missed_appear_snaps_idle_to_dialog():
    rec, sm, ax, *_ = build(dialog=BOX_A)
    assert sm.state is S.IDLE                  # observer never fired
    rec.tick()
    assert sm.state is S.DIALOG
    assert sm.current_dialog() == BOX_A


def test_missed_resolve_snaps_dialog_to_idle():
    rec, sm, ax, *_ = build(dialog=BOX_A)
    sm.on_dialog_change(BOX_A)
    assert sm.state is S.DIALOG
    ax.set_dialog(None)                        # answered by mouse; resolve event dropped
    rec.tick()
    assert sm.state is S.IDLE


def test_no_op_when_state_matches_truth():
    rec, sm, ax, *_ = build(dialog=BOX_A)
    sm.on_dialog_change(BOX_A)
    before = len(sm.tel.transitions)
    rec.tick()                                 # DIALOG believed, box present, same → no-op
    assert sm.state is S.DIALOG
    assert len(sm.tel.transitions) == before   # no transition fired


def test_stash_correction_when_box_changed():
    rec, sm, ax, *_ = build(dialog=BOX_A)
    sm.on_dialog_change(BOX_A)
    ax.set_dialog(BOX_B)                        # different session/type now open
    rec.tick()
    assert sm.state is S.DIALOG
    assert sm.current_dialog() == BOX_B


def test_blind_tick_skips_a11y_when_background_grounded():
    # appstate believes FOREGROUND, but frontmost is another app → ground to BACKGROUND, skip reads.
    rec, sm, ax, system, appstate = build(front="org.mozilla.firefox", dialog=BOX_A, fg=True)
    sm.on_dialog_change(BOX_A)                  # got the box while it was foreground
    assert sm.state is S.DIALOG
    rec.tick()
    assert appstate.is_foreground is False      # grounded to background
    assert sm.state is S.DIALOG                 # NOT false-resolved despite find_dialog→None risk


def test_missed_activate_is_repaired_then_reads():
    # appstate stuck BACKGROUND, but VS Code IS frontmost with a box → repair activate, then appear.
    rec, sm, ax, system, appstate = build(front=VSCODE, dialog=BOX_A, fg=False)
    assert appstate.is_foreground is False
    rec.tick()
    assert appstate.is_foreground is True       # missed activate repaired
    assert sm.state is S.DIALOG                  # ...and then the dialog reconciled in the same tick


def test_tabs_snapshot_refreshed_on_foreground_tick():
    tabs = (Tab(title="a", active=True, badge=0, is_claude=True),
            Tab(title="b", active=False, badge=2, is_claude=True))
    rec, sm, ax, *_ = build(tabs=tabs)
    rec.tick()
    assert rec.tabs == tabs


def test_tabs_not_refreshed_while_background():
    tabs = (Tab(title="a", active=True, badge=0, is_claude=True),)
    rec, sm, ax, system, appstate = build(front="org.mozilla.firefox", tabs=tabs, fg=False)
    rec.tick()
    assert rec.tabs == ()                        # never read the tree while blind
