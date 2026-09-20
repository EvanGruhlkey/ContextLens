from benchmarks.filter_eval import analyze, run_condition


def test_paired_filter_eval_reduces_injected_tokens(tmp_path):
    reports = [
        run_condition("baseline", tmp_path / "baseline"),
        run_condition("jev_observations", tmp_path / "jev", expand_structure=False),
        run_condition("contextlens", tmp_path / "full", expand_structure=True),
    ]
    summary = analyze(reports)
    assert summary["success_gate"]["injected_tool_output_decreases"]
    full = summary["conditions"]["contextlens"]
    baseline = summary["conditions"]["baseline"]
    assert full["injected_tool_output_tokens"] < baseline["injected_tool_output_tokens"]
    assert full["jev_input_tokens"] > 0
    assert full["coding_model_input_tokens"] is None
    assert full["recovery_calls"] >= 0
