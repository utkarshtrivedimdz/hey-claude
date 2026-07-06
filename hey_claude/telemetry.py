"""Telemetry / tuning data (FR-7). Append-only JSONL, local only, redactable.

Three record types share an envelope: `wake`, `turn`, `correction` (see
docs/ARCHITECTURE.md §6). Corrections are separate lines linked by `turn_id`, so
the log is never rewritten. `now`/`new_id` are injectable for deterministic tests.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class Telemetry:
    SCHEMA = 1

    def __init__(
        self,
        cfg,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        new_id: Callable[[], str] = lambda: uuid.uuid4().hex[:8],
    ):
        self.cfg = cfg
        self._now = now
        self._new_id = new_id
        self.enabled = cfg.telemetry_enabled
        self.log_dir = Path(cfg.telemetry_log_dir).expanduser()
        self.session_id = "s_" + new_id()
        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._prune()

    # ---- redaction -------------------------------------------------------
    def _redact(self, text: Optional[str]):
        mode = self.cfg.telemetry_store_prompt_text
        if text is None or mode == "off":
            return None
        if mode == "full":
            return text
        if mode == "hash":
            return hashlib.sha256(text.encode()).hexdigest()[:12]
        if mode == "length_only":
            return {"len": len(text)}
        return None

    # ---- writing ---------------------------------------------------------
    def _path(self, dt: datetime) -> Path:
        return self.log_dir / f"events-{dt.astimezone(timezone.utc):%Y-%m-%d}.jsonl"

    def _emit(self, rec: dict) -> None:
        if not self.enabled:
            return
        dt = self._now()
        rec = {"schema": self.SCHEMA, "ts": _iso(dt), "session_id": self.session_id, **rec}
        with self._path(dt).open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _prune(self) -> None:
        keep = self.cfg.telemetry_retention_days
        if keep <= 0:
            return
        cutoff = self._now().timestamp() - keep * 86400
        for f in self.log_dir.glob("events-*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass

    # ---- public API ------------------------------------------------------
    def log_wake(self, score, threshold, accepted, followed_through=None, note=None) -> None:
        self._emit({
            "event": "wake", "score": round(float(score), 4), "threshold": threshold,
            "accepted": bool(accepted), "followed_through": followed_through, "note": note,
        })

    def log_turn(
        self, *, outcome, command=None, prefix=None, box_pre=None, box_post=None,
        strip_chars=0, latency_ms=None, warm=True, cold_start=False, bootstrap_ms=None,
        focus_gate=None, sentence_done=None, errors=None,
    ) -> str:
        turn_id = "t_" + self._new_id()
        self._emit({
            "event": "turn", "turn_id": turn_id, "warm": warm,
            "bootstrap": {"cold_start": cold_start, "ms": bootstrap_ms, "focus_gate": focus_gate},
            "dictation": {"sentence_done": sentence_done},
            "command": {
                "matched": command, "prefix": prefix,
                "box_pre_strip": self._redact(box_pre),
                "box_post_strip": self._redact(box_post),
                "strip_chars": strip_chars,
            },
            "outcome": outcome,
            "latency_ms": {"wake_to_action": latency_ms},
            "errors": errors or [],
        })
        return turn_id

    def log_correction(self, turn_id, signal, within_ms, inferred="misfire") -> None:
        self._emit({
            "event": "correction", "turn_id": turn_id, "signal": signal,
            "within_ms": int(within_ms), "inferred": inferred,
        })

    def log_transition(self, frm, to, reason, mono, illegal=False) -> None:
        # State-change trace (FR-6 / HARDENING-PLAN Phase 1). Cheap: ~3 records/turn.
        self._emit({
            "event": "state_transition", "from": frm, "to": to, "reason": reason,
            "mono": round(float(mono), 3), "illegal": bool(illegal),
        })
