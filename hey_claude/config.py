"""Config loading (FR-5). TOML → Config dataclass, with defaults.

Python 3.11 tomllib is read-only, which is all we need. Unknown keys are ignored;
missing keys fall back to defaults, so a partial config.toml is fine.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


def _default_words() -> dict:
    return {
        "send": ["send", "submit", "go", "enter"],
        "cancel": ["cancel", "scratch that", "nevermind", "never mind", "clear", "clear all"],
        "stop": ["stop", "interrupt", "abort"],
    }


def _default_placeholders() -> list:
    # An empty box reports its PLACEHOLDER as AXValue (Q11a sub-finding), not "".
    return [
        "Queue another message…",
        "Reply to Claude…",
        "Send a message…",
        "Ask Claude anything…",
        "How can I help you today?",
    ]


def _default_keymap() -> dict:
    # macOS virtual key codes (verified: Esc/Cmd+Esc=53, Return=36, Delete=51)
    # (Cmd+D removed — dictation is driven by AXPress on the Voice-dictation button, not a
    #  keystroke, which collided with VS Code's multi-cursor shortcut. See DICTATION-AX-PLAN.)
    return {"cmd_esc": 53, "esc": 53, "ret": 36, "backspace": 51, "a": 0}


@dataclass
class Config:
    # wake
    wake_phrase: str = "hey jarvis"            # display label; actual detection uses the model
                                               # below, or the pretrained fallback (no trained
                                               # "hey claude" model yet — you say "hey jarvis")
    wake_model: str = ""                       # path to a trained .onnx; empty => pretrained fallback
    wake_pretrained_fallback: str = "hey_jarvis"
    # onnx is REQUIRED on macOS: tflite_runtime has no arm64 wheels, and openWakeWord's
    # default 'tflite' framework silently zeroes the melspec/embedding preprocessor when
    # the runtime is missing → all scores 0.0. Verified 2026-07-06.
    wake_inference_framework: str = "onnx"
    wake_threshold: float = 0.5
    wake_log_floor: float = 0.3                # log near-miss scores >= this (threshold tuning)
    mic_device: Optional[Any] = None

    # commands (Option A)
    command_prefix: list = field(default_factory=lambda: ["okay", "ok"])
    command_words: dict = field(default_factory=_default_words)
    placeholders: list = field(default_factory=_default_placeholders)

    # press-by-name: "<prefix> press <label>" AXPresses the control whose label contains
    # <label>. Searches these AX roles (buttons + Claude Code dialog radio options).
    press_verbs: list = field(default_factory=lambda: ["press"])
    press_roles: list = field(default_factory=lambda: ["AXButton", "AXRadioButton"])

    # dictation fixups: mishearing → correction (case-insensitive, whole-word)
    fixups: dict = field(default_factory=dict)

    # dictation button (ground truth): hey-claude AXPresses this VS Code panel button and reads
    # its AXDescription to know if recording is on. See DICTATION-AX-PLAN.md.
    dictation_button_desc_off: str = "Voice dictation"   # AXDescription when OFF
    dictation_button_desc_on: str = "Stop recording"     # AXDescription when ON (blue)

    # timeouts / windows. (No disarm/silence timeout: the Voice-dictation button is the
    # sole authority for the dictation lifetime — a turn ends on a command word or a
    # button-off event, never a silence timer. See DICTATION-AX-PLAN.md.)
    arm_timeout_s: float = 8.0
    correction_window_ms: int = 5000

    # target (focus-safety gate)
    target_bundle_id: str = "com.microsoft.VSCode"
    target_workspace: str = ""          # absolute path to your .code-workspace (set in config.toml)
    target_title_substr: str = ""       # substring of the target window title; empty => focus gate fails closed

    keymap: dict = field(default_factory=_default_keymap)

    # dialog sensing (Phase 1): detect Claude's approval/choice boxes and announce them.
    # reconcile_interval_s is the periodic backstop sweep (§5) — foreground-gated re-read that
    # snaps the FSMs to UI truth if an event was dropped; 0 disables it (events only).
    dialog_enabled: bool = True
    dialog_announce_sound: str = "Ping"        # macOS system sound played when a box appears
    dialog_reconcile_interval_s: float = 2.5   # 0 = off

    # menu bar (UI): a status-bar icon whose click toggles wake listening on/off.
    # "off" fully stops the wake listener so macOS releases the mic. Set false to run
    # headless (no NSStatusItem, original bare run loop).
    menubar_enabled: bool = True

    # telemetry (FR-7)
    telemetry_enabled: bool = True
    telemetry_retention_days: int = 30
    telemetry_store_prompt_text: str = "full"  # full | hash | length_only | off
    telemetry_log_dir: str = "~/Library/Logs/hey-claude"

    def validate(self) -> "Config":
        if self.telemetry_store_prompt_text not in ("full", "hash", "length_only", "off"):
            raise ValueError(
                f"telemetry.store_prompt_text invalid: {self.telemetry_store_prompt_text!r}"
            )
        if not 0.0 <= self.wake_threshold <= 1.0:
            raise ValueError(f"wake.threshold out of range: {self.wake_threshold}")
        if not isinstance(self.command_words, dict) or not self.command_words:
            raise ValueError("commands.words must be a non-empty table")
        if not self.dictation_button_desc_off or not self.dictation_button_desc_on:
            raise ValueError("dictation.button_desc_off/on must be non-empty")
        return self


def load(path: Optional[str] = None) -> Config:
    """Load config.toml if present, overlaying onto defaults. Returns a validated Config."""
    cfg = Config()
    if not path:
        return cfg.validate()
    p = Path(path).expanduser()
    if not p.exists():
        return cfg.validate()
    data = tomllib.loads(p.read_text())

    wake = data.get("wake", {})
    cfg.wake_phrase = wake.get("phrase", cfg.wake_phrase)
    cfg.wake_model = wake.get("model", cfg.wake_model)
    cfg.wake_pretrained_fallback = wake.get("pretrained_fallback", cfg.wake_pretrained_fallback)
    cfg.wake_inference_framework = wake.get("framework", cfg.wake_inference_framework)
    cfg.wake_threshold = float(wake.get("threshold", cfg.wake_threshold))
    cfg.wake_log_floor = float(wake.get("log_floor", cfg.wake_log_floor))
    cfg.mic_device = wake.get("mic_device", cfg.mic_device)

    cmd = data.get("commands", {})
    cfg.command_prefix = cmd.get("prefix", cfg.command_prefix)
    cfg.command_words = cmd.get("words", cfg.command_words)
    cfg.placeholders = cmd.get("placeholders", cfg.placeholders)
    cfg.press_verbs = cmd.get("press_verbs", cfg.press_verbs)
    cfg.press_roles = cmd.get("press_roles", cfg.press_roles)

    cfg.fixups = data.get("fixups", cfg.fixups)

    dic = data.get("dictation", {})
    cfg.dictation_button_desc_off = dic.get("button_desc_off", cfg.dictation_button_desc_off)
    cfg.dictation_button_desc_on = dic.get("button_desc_on", cfg.dictation_button_desc_on)

    tmo = data.get("timeouts", {})
    cfg.arm_timeout_s = float(tmo.get("arm_s", cfg.arm_timeout_s))
    cfg.correction_window_ms = int(tmo.get("correction_window_ms", cfg.correction_window_ms))

    tgt = data.get("target", {})
    cfg.target_bundle_id = tgt.get("bundle_id", cfg.target_bundle_id)
    cfg.target_workspace = tgt.get("workspace", cfg.target_workspace)
    cfg.target_title_substr = tgt.get("title_substr", cfg.target_title_substr)

    cfg.keymap = {**cfg.keymap, **data.get("keymap", {})}

    dlg = data.get("dialog", {})
    cfg.dialog_enabled = bool(dlg.get("enabled", cfg.dialog_enabled))
    cfg.dialog_announce_sound = dlg.get("announce_sound", cfg.dialog_announce_sound)
    cfg.dialog_reconcile_interval_s = float(
        dlg.get("reconcile_interval_s", cfg.dialog_reconcile_interval_s))

    mb = data.get("menubar", {})
    cfg.menubar_enabled = bool(mb.get("enabled", cfg.menubar_enabled))

    tel = data.get("telemetry", {})
    cfg.telemetry_enabled = bool(tel.get("enabled", cfg.telemetry_enabled))
    cfg.telemetry_retention_days = int(tel.get("retention_days", cfg.telemetry_retention_days))
    cfg.telemetry_store_prompt_text = tel.get("store_prompt_text", cfg.telemetry_store_prompt_text)
    cfg.telemetry_log_dir = tel.get("log_dir", cfg.telemetry_log_dir)

    return cfg.validate()
