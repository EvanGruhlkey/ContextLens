from __future__ import annotations

from benchmarks.evidence_analysis import analyze_pairs


def row(policy: str, success: bool, tokens: int | None) -> dict:
    return {
        "case": "case",
        "trial": 0,
        "policy": policy,
        "verification": {"success": success},
        "status": "completed",
        "input_tokens": tokens,
        "output_tokens": 10,
    }


def test_quality_regression_rejects_policy_despite_lower_tokens() -> None:
    result = analyze_pairs([row("full", True, 1000), row("dependency", False, 100)])
    assert result["deployment_policy"] == "full"
    assert result["observed_quality_regressions"]
    assert result["provider_token_reduction"] > 0


def test_missing_usage_prevents_economics_claim() -> None:
    result = analyze_pairs([row("full", True, 1000), row("dependency", True, None)])
    assert result["deployment_policy"] == "full"
    assert result["provider_token_reduction"] is None
    assert result["total_provider_tokens_candidate"] is None


def test_infrastructure_failures_never_establish_quality_or_savings() -> None:
    broken = row("dependency", True, 100)
    broken["status"] = "invalid_sandbox_configuration"
    result = analyze_pairs([row("full", True, 1000), broken])
    assert result["deployment_policy"] == "full"
    assert result["provider_token_reduction"] is None
    assert result["statistical_quality_preservation_claim"] is None
