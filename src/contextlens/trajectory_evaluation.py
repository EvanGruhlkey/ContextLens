"""Paired whole-trajectory metrics including ContextLens decision overhead."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class TrajectoryRun:
    task_id: str
    trial: int
    policy: str
    success: bool
    tests_passed: bool
    provider_input_tokens: int
    provider_output_tokens: int
    tool_calls: int
    duration_ms: float
    recovery_calls: int
    rereads: int
    jev_input_tokens: int
    jev_output_tokens: int
    jev_cost: str
    patch_sha256: str

    def __post_init__(self) -> None:
        if not self.task_id or self.policy not in {"baseline", "control"}:
            raise ValueError("trajectory identity is invalid")
        integers = (
            self.trial,
            self.provider_input_tokens,
            self.provider_output_tokens,
            self.tool_calls,
            self.recovery_calls,
            self.rereads,
            self.jev_input_tokens,
            self.jev_output_tokens,
        )
        if any(type(value) is not int or value < 0 for value in integers):
            raise ValueError("trajectory counters must be nonnegative integers")
        if self.duration_ms < 0 or not self.patch_sha256:
            raise ValueError("trajectory measurements are invalid")
        try:
            if Decimal(self.jev_cost) < 0:
                raise ValueError("Jev cost must be nonnegative")
        except Exception as error:
            raise ValueError("Jev cost must be numeric") from error


def compare_trajectories(runs: list[TrajectoryRun]) -> dict[str, Any]:
    grouped: dict[tuple[str, int], dict[str, TrajectoryRun]] = {}
    for item in runs:
        policies = grouped.setdefault((item.task_id, item.trial), {})
        if item.policy in policies:
            raise ValueError("duplicate trajectory policy")
        policies[item.policy] = item
    pairs = [
        policies
        for policies in grouped.values()
        if set(policies) == {"baseline", "control"}
    ]
    baseline_input = sum(
        pair["baseline"].provider_input_tokens + pair["baseline"].jev_input_tokens
        for pair in pairs
    )
    control_input = sum(
        pair["control"].provider_input_tokens + pair["control"].jev_input_tokens
        for pair in pairs
    )
    baseline_output = sum(
        pair["baseline"].provider_output_tokens + pair["baseline"].jev_output_tokens
        for pair in pairs
    )
    control_output = sum(
        pair["control"].provider_output_tokens + pair["control"].jev_output_tokens
        for pair in pairs
    )
    regressions = sorted(
        pair["baseline"].task_id
        for pair in pairs
        if pair["baseline"].success and not pair["control"].success
    )
    complete = bool(pairs) and len(pairs) * 2 == len(runs)
    reduction = (
        round((1 - control_input / baseline_input) * 100, 2) if baseline_input else None
    )
    return {
        "complete_pairs": len(pairs),
        "complete": complete,
        "successes": {
            policy: sum(pair[policy].success for pair in pairs)
            for policy in ("baseline", "control")
        },
        "tests_passed": {
            policy: sum(pair[policy].tests_passed for pair in pairs)
            for policy in ("baseline", "control")
        },
        "total_input_tokens": {
            "baseline": baseline_input,
            "control": control_input,
        },
        "total_output_tokens": {
            "baseline": baseline_output,
            "control": control_output,
        },
        "input_reduction_percent": reduction,
        "tool_calls": {
            policy: sum(pair[policy].tool_calls for pair in pairs)
            for policy in ("baseline", "control")
        },
        "recovery_calls": sum(pair["control"].recovery_calls for pair in pairs),
        "rereads": {
            policy: sum(pair[policy].rereads for pair in pairs)
            for policy in ("baseline", "control")
        },
        "jev_cost": str(sum(Decimal(pair["control"].jev_cost) for pair in pairs)),
        "quality_regressions": regressions,
        "observed_sample_meets_goal": bool(
            complete
            and not regressions
            and all(pair["control"].success for pair in pairs)
            and reduction is not None
            and reduction > 0
        ),
    }
