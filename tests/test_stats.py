"""stats.py metric math over a synthetic event set (ARCHITECTURE §6.5)."""
from scripts import stats


def _wake(ft):
    return {"event": "wake", "accepted": True, "followed_through": ft, "score": 0.7 if ft else 0.52}


def _turn(tid, outcome="sent", latency=800, cold=False):
    return {"event": "turn", "turn_id": tid, "outcome": outcome, "warm": not cold,
            "latency_ms": {"wake_to_action": latency}, "bootstrap": {"cold_start": cold}}


def test_compute_metrics():
    wakes = [_wake(True)] * 8 + [_wake(False)] * 2           # 10 accepted, 2 false
    turns = [
        _turn("t1", latency=500), _turn("t2", latency=800), _turn("t3", latency=1000),
        _turn("t4", outcome="timeout"),
    ]
    corrections = [{"event": "correction", "turn_id": "t3", "inferred": "misfire"}]

    m = stats.compute(wakes, turns, corrections)

    assert m["false_trigger_rate"] == 0.2                    # 2 / 10
    assert m["sends"] == 3
    assert m["command_precision"] == round(1 - 1 / 3, 4)     # one of three sends misfired
    assert m["latency_p50_ms"] == 800
    assert m["latency_p95_ms"] == 1000
    assert m["cold_start_rate"] == 0.0
    assert m["disarm_rate"] == 0.1                           # 1 timeout / 10 accepted
