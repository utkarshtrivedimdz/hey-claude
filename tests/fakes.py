"""Test doubles for the I/O ports — record ops, script returns. No pyobjc."""
from __future__ import annotations

from hey_claude.bootstrap import BootstrapResult


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

    def cmd_esc(self): self.ops.append(("cmd_esc",))
    def esc(self): self.ops.append(("esc",))
    def type_text(self, s): self.ops.append(("type", s))
    def backspace(self, n): self.ops.append(("backspace", n))
    def ret(self): self.ops.append(("ret",))
    def clear(self): self.ops.append(("clear",))

    def names(self):
        return [o[0] for o in self.ops]


class FakeAX:
    """Fakes the box textarea observer AND the dictation button (ground truth).

    `press_dictation` flips the button and, while observing, fires the dictation
    callback — simulating the real AXTitleChanged event. `feed_dictation` simulates
    an EXTERNAL toggle (user clicks the mic). `button_present=False` makes
    `dictation_on` return None (the fail-loud path).
    """

    def __init__(self, value: str = "", dict_on: bool = False, button_present: bool = True,
                 press_found: bool = True, dialog=None):
        self.value = value
        self.box_observing = False
        self._box_cb = None
        self._dict_on = dict_on
        self.dict_observing = False
        self._dict_cb = None
        self.button_present = button_present
        self.press_found = press_found   # what press_by_name returns
        self.pressed: list = []          # (keyword, roles) recorded per press_by_name call
        self.ops: list = []   # dictation ops (press_dictation)
        self._dialog = dialog            # what find_dialog() returns (a DialogBox or None)
        self.dialog_observing = False
        self._dialog_cb = None

    def set_manual_a11y(self): pass
    def read_box(self): return self.value
    def read_box_settled(self): return self.value

    def press_by_name(self, keyword, roles=("AXButton", "AXRadioButton")):
        self.pressed.append((keyword, tuple(roles)))
        return self.press_found

    # box textarea observer
    def observe_box(self, on_change):
        self.box_observing = True
        self._box_cb = on_change
        return True

    def stop_observing_box(self):
        self.box_observing = False
        self._box_cb = None

    def feed(self, text: str):
        """Simulate a box value-changed event (call the box observer callback)."""
        self.value = text
        if self.box_observing and self._box_cb:
            self._box_cb(text)

    # dictation button (ground truth)
    def dictation_on(self):
        return None if not self.button_present else self._dict_on

    def press_dictation(self):
        self.ops.append("press_dictation")
        if not self.button_present:
            return
        self._dict_on = not self._dict_on
        if self.dict_observing and self._dict_cb:   # simulate the AXTitleChanged event
            self._dict_cb(self._dict_on)

    def observe_dictation(self, on_change):
        if not self.button_present:
            return False
        self.dict_observing = True
        self._dict_cb = on_change
        return True

    def stop_observing_dictation(self):
        self.dict_observing = False
        self._dict_cb = None

    def feed_dictation(self, is_on: bool):
        """Simulate an EXTERNAL button toggle (user clicks the mic on/off)."""
        self._dict_on = is_on
        if self.dict_observing and self._dict_cb:
            self._dict_cb(is_on)

    # dialog detection (Phase 1)
    def find_dialog(self, settle_s: float = 0.3):
        return self._dialog

    def set_dialog(self, box):
        """Set what find_dialog() reads (does NOT fire the observer — for reconciler/Path-B)."""
        self._dialog = box

    def observe_dialog(self, on_change):
        self.dialog_observing = True
        self._dialog_cb = on_change
        return True

    def stop_observing_dialog(self):
        self.dialog_observing = False
        self._dialog_cb = None

    def feed_dialog(self, box):
        """Simulate an AXFocusedUIElementChanged dialog event (fires the observer callback)."""
        self._dialog = box
        if self.dialog_observing and self._dialog_cb:
            self._dialog_cb(box)


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
        self.dialogs: list = []
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

    def log_dialog(self, event, box_type=None, n_options=None, foreground=None):
        self.dialogs.append(dict(event=event, box_type=box_type,
                                 n_options=n_options, foreground=foreground))


class FakeSystem:
    """For bootstrap tests. Scriptable frontmost/title/running + tabs snapshot."""
    def __init__(self, running=True, front="com.microsoft.VSCode",
                 title="proj — geofast (Workspace)", tabs=()):
        self.running = running
        self.front = front
        self.title = title
        self.tabs = tuple(tabs)   # scriptable list_tabs() return
        self.ops: list = []

    def frontmost_bundle_id(self): return self.front
    def window_title(self): return self.title
    def is_app_running(self): return self.running
    def launch_app(self): self.ops.append("launch"); self.running = True
    def open_path(self, path): self.ops.append(("open", path))
    def raise_app(self): self.ops.append("raise"); self.front = "com.microsoft.VSCode"
    def list_tabs(self): return self.tabs
