"""Live AX / system smoke tests. Marked `integration` → skipped by default
(need a running, focused VS Code and pyobjc). Run: pytest -m integration.

These are the seeds from the 2026-07-06 feasibility probes, promoted into the suite.
"""
import pytest

pytest.importorskip("ApplicationServices")

from hey_claude import config
from hey_claude.ax import RealAX
from hey_claude.system import RealSystem

pytestmark = pytest.mark.integration


@pytest.fixture
def cfg():
    return config.load("config.toml")


def test_read_box_does_not_raise(cfg):
    ax = RealAX(cfg.target_bundle_id)
    ax.set_manual_a11y()
    val = ax.read_box()
    assert val is None or isinstance(val, str)


def test_system_queries(cfg):
    s = RealSystem(cfg)
    assert isinstance(s.is_app_running(), bool)
    fb = s.frontmost_bundle_id()
    assert fb is None or isinstance(fb, str)
    t = s.window_title()
    assert t is None or isinstance(t, str)


def test_keys_construct(cfg):
    from hey_claude.keys import RealKeys
    RealKeys(cfg.keymap)  # constructing must not raise; we don't fire keys in CI
