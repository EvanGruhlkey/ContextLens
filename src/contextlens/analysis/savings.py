"""Production workload savings from verified context effects."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from contextlens.analysis.paired import (
    EffectVerdict,
    EvidenceScope,
    PairedEffect,
)


class SavingsAction(StrEnum):
    """Operational recommendation for a context source."""

    KEEP = "keep"
    REMOVE = "remove"
    INVESTIGATE = "investigate"


@dataclass(frozen=True, slots=True)
class Workload:
    """A real agent workload over which savings are projected."""

    runs_per_day: float
    projection_days: int = 30
    experiment_cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.runs_per_day) or self.runs_per_day < 0:
            raise ValueError("runs_per_day must be finite and nonnegative")
        if self.projection_days < 1:
            raise ValueError("projection_days must be positive")
        if self.experiment_cost_usd < 0:
            raise ValueError("experiment_cost_usd cannot be negative")

    @property
    def projected_runs(self) -> float:
        return self.runs_per_day * self.projection_days


@dataclass(frozen=True, slots=True)
class SavingsRecommendation:
    """Evidence-backed action and projected operational impact."""

    source_id: str
    name: str
    action: SavingsAction
    reason: str
    evidence_scope: str
    context_effect: float
    removal_quality_change: float
    removal_quality_low: float
    removal_quality_high: float
    per_run_input_tokens_saved: float
    per_run_output_tokens_saved: float
    per_run_cost_saved_usd: float
    per_run_latency_saved_seconds: float
    projected_runs: float
    projected_input_tokens_saved: float
    projected_output_tokens_saved: float
    projected_gross_cost_saved_usd: float
    projected_net_cost_saved_usd: float
    projected_latency_saved_seconds: float
    break_even_runs: int | None
    warnings: tuple[str, ...]


class SavingsAnalyzer:
    """Translate paired target-model effects into production decisions."""

    def recommend(
        self,
        effect: PairedEffect,
        workload: Workload,
        *,
        source_id: str | None = None,
        name: str | None = None,
    ) -> SavingsRecommendation:
        identifier = source_id or effect.ablated_variant_id
        warnings = list(effect.warnings)
        if effect.evidence_scope is not EvidenceScope.TARGET_MODEL:
            action = SavingsAction.INVESTIGATE
            reason = "screening-model evidence requires target-model verification"
            warnings.append("production savings require target-model evidence")
        elif effect.verdict is EffectVerdict.HELPFUL:
            action = SavingsAction.KEEP
            reason = "removing this context caused a verified quality loss"
        elif effect.verdict in {EffectVerdict.HARMFUL, EffectVerdict.NEUTRAL}:
            action = SavingsAction.REMOVE
            reason = (
                "removal improved quality"
                if effect.verdict is EffectVerdict.HARMFUL
                else "removal preserved quality within the equivalence tolerance"
            )
        else:
            action = SavingsAction.INVESTIGATE
            reason = "the quality effect is still uncertain"

        realized = action is SavingsAction.REMOVE
        runs = workload.projected_runs if realized else 0.0
        input_saved = max(0.0, effect.input_tokens_saved_by_ablation)
        output_saved = max(0.0, effect.output_tokens_saved_by_ablation)
        cost_saved = max(0.0, effect.cost_saved_by_ablation_usd)
        latency_saved = max(0.0, effect.latency_saved_by_ablation_seconds)
        gross = cost_saved * runs
        net = gross - workload.experiment_cost_usd if realized else 0.0
        break_even = (
            math.ceil(workload.experiment_cost_usd / cost_saved)
            if realized and cost_saved > 0
            else None
        )
        if realized and input_saved == 0 and cost_saved == 0:
            warnings.append(
                "removal is quality-safe but has no measured token or cost saving"
            )
        return SavingsRecommendation(
            source_id=identifier,
            name=name or identifier,
            action=action,
            reason=reason,
            evidence_scope=effect.evidence_scope.value,
            context_effect=effect.effect,
            removal_quality_change=-effect.effect,
            removal_quality_low=-effect.confidence_high,
            removal_quality_high=-effect.confidence_low,
            per_run_input_tokens_saved=input_saved,
            per_run_output_tokens_saved=output_saved,
            per_run_cost_saved_usd=cost_saved,
            per_run_latency_saved_seconds=latency_saved,
            projected_runs=runs,
            projected_input_tokens_saved=input_saved * runs,
            projected_output_tokens_saved=output_saved * runs,
            projected_gross_cost_saved_usd=gross,
            projected_net_cost_saved_usd=net,
            projected_latency_saved_seconds=latency_saved * runs,
            break_even_runs=break_even,
            warnings=tuple(dict.fromkeys(warnings)),
        )

