"""Phase 0 — AppState FSM (foreground gate) + Tab snapshot projection. Pure, pyobjc-free."""
import pytest

from hey_claude.appstate import (
    AppState, AppStateMachine, LEGAL, IllegalTransition,
    Tab, TabStatus, tab_status,
)


def make(strict=True, record=False):
    changes: list = []
    cb = (lambda old, new: changes.append((old, new))) if record else None
    return AppStateMachine(on_change=cb, strict=strict), changes


# ---- FSM: legal table + chokepoint --------------------------------------

def test_legal_table_is_exhaustive():
    # Every state is a key in LEGAL; every target is a real state.
    assert set(LEGAL.keys()) == set(AppState)
    for src, dsts in LEGAL.items():
        assert dsts <= set(AppState)


def test_starts_unknown():
    sm, _ = make()
    assert sm.state is AppState.UNKNOWN
    assert sm.is_foreground is False


def test_first_observation_resolves_unknown():
    for event, expected in [
        ("on_activate", AppState.FOREGROUND),
        ("on_deactivate", AppState.BACKGROUND),
        ("on_terminate", AppState.NOT_RUNNING),
        ("on_launch", AppState.BACKGROUND),
    ]:
        sm, _ = make()
        getattr(sm, event)()
        assert sm.state is expected, event


def test_foreground_background_round_trip():
    sm, _ = make()
    sm.on_activate()
    assert sm.state is AppState.FOREGROUND and sm.is_foreground
    sm.on_deactivate()
    assert sm.state is AppState.BACKGROUND and not sm.is_foreground
    sm.on_activate()
    assert sm.state is AppState.FOREGROUND


def test_launch_lands_in_background_not_foreground():
    # Launch alone never trusts a11y — a DidActivate must follow to reach FOREGROUND.
    sm, _ = make()
    sm.on_terminate()
    assert sm.state is AppState.NOT_RUNNING
    sm.on_launch()
    assert sm.state is AppState.BACKGROUND
    sm.on_activate()
    assert sm.state is AppState.FOREGROUND


def test_terminate_from_either_running_state():
    for reach in ("on_activate", "on_deactivate"):
        sm, _ = make()
        getattr(sm, reach)()
        sm.on_terminate()
        assert sm.state is AppState.NOT_RUNNING


def test_duplicate_event_is_idempotent_noop_not_illegal():
    # NSWorkspace can deliver duplicate notifications; a same-state event must not flag illegal.
    sm, changes = make(strict=True, record=True)
    sm.on_activate()
    sm.on_activate()  # would be FOREGROUND→FOREGROUND — must be a silent no-op, not a raise
    assert sm.state is AppState.FOREGROUND
    assert changes == [(AppState.UNKNOWN, AppState.FOREGROUND)]  # only the real change fired


def test_illegal_transition_raises_in_strict_mode():
    sm, _ = make(strict=True)
    sm.on_terminate()  # UNKNOWN → NOT_RUNNING (legal)
    with pytest.raises(IllegalTransition):
        sm.on_activate()  # NOT_RUNNING → FOREGROUND is not declared (must launch first)


def test_illegal_transition_commits_but_flags_when_not_strict():
    # Prod (strict=False): a missed intermediate event still lands the FSM on reality.
    sm, _ = make(strict=False)
    sm.on_terminate()
    sm.on_activate()  # illegal NOT_RUNNING→FOREGROUND, but committed so we track truth
    assert sm.state is AppState.FOREGROUND


def test_on_change_fires_with_old_and_new():
    sm, changes = make(record=True)
    sm.on_activate()
    sm.on_deactivate()
    assert changes == [
        (AppState.UNKNOWN, AppState.FOREGROUND),
        (AppState.FOREGROUND, AppState.BACKGROUND),
    ]


# ---- Tabs snapshot projection -------------------------------------------

def test_tab_status_active():
    t = Tab(title="claude", active=True, badge=0, is_claude=True)
    assert tab_status(t) is TabStatus.ACTIVE


def test_tab_status_active_wins_over_badge():
    # The active tab is ACTIVE regardless of any stale badge on it.
    t = Tab(title="claude", active=True, badge=3, is_claude=True)
    assert tab_status(t) is TabStatus.ACTIVE


def test_tab_status_bg_quiet_vs_attention():
    quiet = Tab(title="a", active=False, badge=0, is_claude=True)
    loud = Tab(title="b", active=False, badge=2, is_claude=True)
    assert tab_status(quiet) is TabStatus.BG_QUIET
    assert tab_status(loud) is TabStatus.BG_ATTENTION


def test_tab_is_frozen():
    t = Tab(title="a", active=False, badge=0, is_claude=False)
    with pytest.raises(Exception):
        t.title = "b"  # frozen dataclass — snapshots are immutable
