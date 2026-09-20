from benchmarks.controller_loop import summarize


def test_loop_summary_counts_all_decision_overhead():
    report = summarize(
        [
            {
                "expected": "search",
                "selected": "search",
                "fallback": None,
                "calls": 2,
                "input_tokens": 100,
                "output_tokens": 20,
                "latency_ms": 4.0,
                "cost": "0.01",
                "deferred": 0,
            },
            {
                "expected": "read",
                "selected": None,
                "fallback": "gateway_unavailable",
                "calls": 1,
                "input_tokens": 40,
                "output_tokens": 5,
                "latency_ms": 2.0,
                "cost": "0.02",
                "deferred": 1,
            },
        ]
    )
    assert report["top_1"] == {"correct": 1, "total": 1, "rate": 1.0}
    assert report["fallbacks"] == 1
    assert report["jev_calls"] == 3
    assert report["input_tokens"] == 140
    assert report["deferred_observations"] == 1
    assert report["cost"] == "0.03"
