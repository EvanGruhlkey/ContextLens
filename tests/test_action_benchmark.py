from benchmarks.action_selection import summarize


def test_action_summary_reports_top_one_and_top_k():
    rows = [
        {"gold": "read", "ranking": ["read", "search", "stop"], "input_tokens": 10},
        {"gold": "search", "ranking": ["read", "search", "stop"], "input_tokens": 12},
        {"gold": "stop", "ranking": ["read", "search", "stop"], "input_tokens": 14},
    ]
    assert summarize(rows) == {
        "cases": 3,
        "valid_cases": 3,
        "fallbacks": 0,
        "top_1_correct": 1,
        "top_1_accuracy": 1 / 3,
        "planned_top_1_accuracy": 1 / 3,
        "top_3_correct": 3,
        "top_3_recall": 1.0,
        "planned_top_3_recall": 1.0,
        "input_tokens": 36,
    }


def test_action_summary_does_not_disguise_fallback_as_model_error():
    rows = [
        {"gold": "read", "ranking": ["read"], "input_tokens": 10},
        {"gold": "search", "ranking": [], "input_tokens": None},
    ]
    summary = summarize(rows)
    assert summary["valid_cases"] == 1
    assert summary["fallbacks"] == 1
    assert summary["top_1_accuracy"] == 1.0
    assert summary["planned_top_1_accuracy"] == 0.5
