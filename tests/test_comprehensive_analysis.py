from pathlib import Path

from benchmarks.comprehensive import command_for
from benchmarks.comprehensive_analysis import compare, total_tokens


def row(policy, success=True, status="completed", tokens=100):
    return {
        "case": "task",
        "trial": 0,
        "policy": policy,
        "status": status,
        "verification": {"success": success},
        "input_tokens": tokens,
        "output_tokens": 10,
    }


def test_normal_baseline_has_no_contextlens_tools():
    command = command_for("normal", Path("repo"), Path("state"), "model", "codex")
    assert not any("mcp_servers" in arg for arg in command)
    configured = command_for("full", Path("repo"), Path("state"), "model", "codex")
    assert any("mcp_servers.contextlens.command" in arg for arg in configured)


def test_unknown_usage_never_becomes_zero():
    assert total_tokens([row("normal", tokens=None)]) is None
    assert total_tokens([]) is None


def test_failed_task_tokens_are_included_and_regression_is_visible():
    result = compare(
        [row("normal"), row("dependency", False, tokens=50)], "normal", "dependency"
    )
    assert result["observed_regressions"]
    assert result["paired_candidate_tokens"] == 60
    assert result["quality_preservation_claim"] is None


def test_invalid_runs_do_not_establish_paired_savings():
    result = compare(
        [row("normal"), row("dependency", status="agent_process_error")],
        "normal",
        "dependency",
    )
    assert result["complete_pairs"] == 0
    assert result["matched_provider_token_reduction"] is None
