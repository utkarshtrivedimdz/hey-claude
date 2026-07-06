"""FR-7 telemetry — redaction modes, envelope, correction records."""
import json
from datetime import datetime, timezone

from chotu.config import Config
from chotu.telemetry import Telemetry


def _tel(tmp_path, mode):
    cfg = Config()
    cfg.telemetry_log_dir = str(tmp_path)
    cfg.telemetry_store_prompt_text = mode
    fixed = datetime(2026, 7, 6, 21, 3, 16, 842000, tzinfo=timezone.utc)
    n = {"i": 0}

    def nid():
        n["i"] += 1
        return f"{n['i']:04d}"

    return Telemetry(cfg, now=lambda: fixed, new_id=nid), tmp_path / "events-2026-07-06.jsonl"


def _read(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_redaction_full(tmp_path):
    tel, path = _tel(tmp_path, "full")
    tel.log_turn(outcome="sent", command="send", box_pre="secret hi", box_post="hi")
    rec = _read(path)[-1]
    assert rec["command"]["box_pre_strip"] == "secret hi"
    assert rec["event"] == "turn" and rec["schema"] == 1 and rec["session_id"].startswith("s_")


def test_redaction_hash(tmp_path):
    tel, path = _tel(tmp_path, "hash")
    tel.log_turn(outcome="sent", box_pre="secret")
    v = _read(path)[-1]["command"]["box_pre_strip"]
    assert isinstance(v, str) and len(v) == 12 and all(c in "0123456789abcdef" for c in v)


def test_redaction_length_only(tmp_path):
    tel, path = _tel(tmp_path, "length_only")
    tel.log_turn(outcome="sent", box_pre="abcdef")
    assert _read(path)[-1]["command"]["box_pre_strip"] == {"len": 6}


def test_redaction_off(tmp_path):
    tel, path = _tel(tmp_path, "off")
    tel.log_turn(outcome="sent", box_pre="secret")
    assert _read(path)[-1]["command"]["box_pre_strip"] is None


def test_wake_and_correction_records(tmp_path):
    tel, path = _tel(tmp_path, "full")
    tel.log_wake(0.72, 0.5, True, followed_through=True)
    tid = tel.log_turn(outcome="sent")
    tel.log_correction(tid, "stop", 2400)
    recs = _read(path)
    assert recs[0]["event"] == "wake" and recs[0]["accepted"] is True
    assert recs[2]["event"] == "correction" and recs[2]["turn_id"] == tid
    assert recs[2]["within_ms"] == 2400 and recs[2]["inferred"] == "misfire"


def test_state_transition_record(tmp_path):
    tel, path = _tel(tmp_path, "full")
    tel.log_transition("armed", "dictating", "dictation_started", 1234.5678)
    tel.log_transition("idle", "dictating", "bogus", 1235.0, illegal=True)
    recs = _read(path)
    assert recs[0]["event"] == "state_transition"
    assert (recs[0]["from"], recs[0]["to"], recs[0]["reason"]) == ("armed", "dictating", "dictation_started")
    assert recs[0]["mono"] == 1234.568 and recs[0]["illegal"] is False
    assert recs[1]["illegal"] is True
