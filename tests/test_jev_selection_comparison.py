from benchmarks.jev_selection_comparison import compare_reports


def test_comparison_includes_complete_decision_usage():
    full = {
        "summary": {
            "cases": 2,
            "evidence_passes": 2,
            "selection_response_tokens": 500,
            "decision_input_tokens": 2000,
            "decision_output_tokens": 100,
            "gateway_cost": "0.02",
        }
    }
    two_stage = {
        "summary": {
            "cases": 2,
            "evidence_passes": 2,
            "selection_response_tokens": 450,
            "decision_input_tokens": 800,
            "decision_output_tokens": 80,
            "gateway_cost": "0.01",
        }
    }

    comparison = compare_reports(full, two_stage)

    assert comparison["evidence_pass_delta"] == 0
    assert comparison["decision_input_reduction_percent"] == 60.0
    assert comparison["delivered_context_reduction_percent"] == 10.0
    assert comparison["cost_reduction_percent"] == 50.0
