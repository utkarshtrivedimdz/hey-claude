#!/usr/bin/env python3
"""Derive tuning metrics from the FR-7 event log (ARCHITECTURE §6.5).

Usage:  python scripts/stats.py [LOG_DIR]
Default LOG_DIR: ~/Library/Logs/hey-claude
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import median


def _pctl(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def load(log_dir: Path):
    wakes, turns, corrections = [], [], []
    for f in sorted(log_dir.glob("events-*.jsonl")):
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            {"wake": wakes, "turn": turns, "correction": corrections}.get(r.get("event"), []).append(r)
    return wakes, turns, corrections


def compute(wakes, turns, corrections) -> dict:
    accepted = [w for w in wakes if w.get("accepted")]
    resolved = [w for w in accepted if w.get("followed_through") is not None and w.get("note") is None]
    false_accepts = [w for w in resolved if w.get("followed_through") is False]

    sent = [t for t in turns if t.get("outcome") == "sent"]
    misfire_ids = {c["turn_id"] for c in corrections if c.get("inferred") == "misfire"}
    misfired_sends = [t for t in sent if t.get("turn_id") in misfire_ids]

    warm_lat = [
        t["latency_ms"]["wake_to_action"]
        for t in turns
        if t.get("warm") and (t.get("latency_ms") or {}).get("wake_to_action") is not None
    ]
    timeouts = [t for t in turns if t.get("outcome") == "timeout"]
    cold = [t for t in turns if (t.get("bootstrap") or {}).get("cold_start")]

    def rate(a, b):
        return round(len(a) / len(b), 4) if b else None

    return {
        "wakes_total": len(wakes),
        "wakes_accepted": len(accepted),
        "false_trigger_rate": rate(false_accepts, resolved),
        "wake_score_accepted_median": round(median([w["score"] for w in resolved]), 3) if resolved else None,
        "wake_score_falseaccept_median": round(median([w["score"] for w in false_accepts]), 3) if false_accepts else None,
        "turns_total": len(turns),
        "sends": len(sent),
        "command_precision": round(1 - rate(misfired_sends, sent), 4) if sent else None,
        "latency_p50_ms": _pctl(warm_lat, 50),
        "latency_p95_ms": _pctl(warm_lat, 95),
        "cold_start_rate": rate(cold, turns),
        "disarm_rate": rate(timeouts, accepted),
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    log_dir = Path(argv[0]).expanduser() if argv else Path("~/Library/Logs/hey-claude").expanduser()
    if not log_dir.exists():
        print(f"no log dir: {log_dir}")
        return 1
    m = compute(*load(log_dir))
    width = max(len(k) for k in m)
    print(f"hey-claude telemetry — {log_dir}\n" + "-" * 44)
    for k, v in m.items():
        print(f"{k.rjust(width)} : {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
