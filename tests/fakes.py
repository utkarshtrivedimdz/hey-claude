"""Test doubles for the I/O ports — record ops, script returns. No pyobjc."""
from __future__ import annotations

from chotu.bootstrap import BootstrapResult


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeKeys:
    def __init__(self):
        self.ops: list = []

    def cmd_d(self): self.ops.append(("cmd_d",))
    def cmd_esc(self): self.ops.append(("cmd_esc",))
    def esc(self): self.ops.append(("esc",))
    def type_text(self, s): self.ops.append(("type", s))
    def backspace(self, n): self.ops.append(("backspace", n))
    def ret(self): self.ops.append(("ret",))
    def clear(self): self.ops.append(("clear",))

    def names(self):
        return [o[0] for o in self.ops]


class FakeAX:
    def __init__(self, value: str = ""):
        self.value = value
        self.observing = False
        self._cb = None

    def set_manual_a11y(self): pass
    def read_box(self): return self.value

    def start_observing(self, on_change):
        self.observing = True
        self._cb = on_change
        return True

    def stop_observing(self):
        self.observing = False

    def feed(self, text: str):
        """Simulate a value-changed event (call the observer callback)."""
        self.value = text
        if self.observing and self._cb:
            self._cb(text)


class FakeBootstrap:
    def __init__(self, result: BootstrapResult | None = None):
        self.result = result or BootstrapResult(ok=True, cold_start=False, ms=12, focus_gate="pass")
        self.calls = 0

    def ensure_ready(self) -> BootstrapResult:
        self.calls += 1
        return self.result


class FakeTelemetry:
    def __init__(self):
        self.wakes: list = []
        self.turns: list = []
        self.corrections: list = []
        self.transitions: list = []
        self._n = 0

    def log_wake(self, score, threshold, accepted, followed_through=None, note=None):
        self.wakes.append(dict(score=score, accepted=accepted,
                               followed_through=followed_through, note=note))

    def log_turn(self, **kw):
        self._n += 1
        tid = f"t_{self._n}"
        self.turns.append(dict(turn_id=tid, **kw))
        return tid

    def log_correction(self, turn_id, signal, within_ms, inferred="misfire"):
        self.corrections.append(dict(turn_id=turn_id, signal=signal,
                                     within_ms=within_ms, inferred=inferred))

    def log_transition(self, frm, to, reason, mono, illegal=False):
        self.transitions.append(dict(frm=frm, to=to, reason=reason,
                                     mono=mono, illegal=illegal))


class FakeSystem:
    """For bootstrap tests. Scriptable frontmost/title/running."""
    def __init__(self, running=True, front="com.microsoft.VSCode",
                 title="proj — geofast (Workspace)"):
        self.running = running
        self.front = front
        self.title = title
        self.ops: list = []

    def frontmost_bundle_id(self): return self.front
    def window_title(self): return self.title
    def is_app_running(self): return self.running
    def launch_app(self): self.ops.append("launch"); self.running = True
    def open_path(self, path): self.ops.append(("open", path))
    def raise_app(self): self.ops.append("raise"); self.front = "com.microsoft.VSCode"
