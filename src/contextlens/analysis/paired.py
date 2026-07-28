"""Paired baseline-versus-ablation statistical analysis."""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from enum import StrEnum

from contextlens.experiments.evaluation import Evaluation
from contextlens.experiments.model import ReplayResult


class EffectVerdict(StrEnum):
    """Interpretation of a confidence interval for context value."""

    HELPFUL = "helpful"
    HARMFUL = "harmful"
    NEUTRAL = "neutral"
    UNCERTAIN = "uncertain"


class EvidenceScope(StrEnum):
    """Whether measurements came from screening or the deployment model."""

    SCREENING = "screening"
    TARGET_MODEL = "target_model"


@dataclass(frozen=True, slots=True)
class Measurement:
    """Comparable result for one task and repeated trial."""

    task_id: str
    trial_id: str
    variant_id: str
    score: float
    success: bool
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_seconds: float
    evidence_scope: EvidenceScope = EvidenceScope.TARGET_MODEL

    def __post_init__(self) -> None:
        if not self.task_id or not self.trial_id or not self.variant_id:
            raise ValueError("task_id, trial_id, and variant_id cannot be empty")
        if not math.isfinite(self.score):
            raise ValueError("score must be finite")
        for name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cost_usd", self.cost_usd),
            ("latency_seconds", self.latency_seconds),
        ):
            if value < 0:
                raise ValueError(f"{name} cannot be negative")

    @classmethod
    def from_result(
        cls,
        result: ReplayResult,
        evaluation: Evaluation,
        *,
        trial_id: str,
        score_name: str,
        success_name: str = "success",
        evidence_scope: EvidenceScope = EvidenceScope.TARGET_MODEL,
    ) -> Measurement:
        if score_name not in evaluation.scores:
            raise ValueError(f"evaluation has no score {score_name!r}")
        outcome = result.outcome
        return cls(
            task_id=result.task_id,
            trial_id=trial_id,
            variant_id=result.variant_id,
            score=evaluation.scores[score_name],
            success=bool(evaluation.scores.get(success_name, 0.0)),
            input_tokens=(
                outcome.input_tokens
                if outcome is not None and outcome.input_tokens is not None
                else result.context_tokens
            ),
            output_tokens=(
                outcome.output_tokens
                if outcome is not None and outcome.output_tokens is not None
                else 0
            ),
            cost_usd=(
                outcome.cost_usd
                if outcome is not None and outcome.cost_usd is not None
                else 0.0
            ),
            latency_seconds=result.duration_seconds,
            evidence_scope=evidence_scope,
        )


@dataclass(frozen=True, slots=True)
class PairedEffect:
    """Aggregate effect of one context-bearing baseline over an ablation."""

    baseline_variant_id: str
    ablated_variant_id: str
    pair_count: int
    task_count: int
    baseline_mean: float
    ablated_mean: float
    effect: float
    relative_effect: float | None
    confidence_low: float
    confidence_high: float
    verdict: EffectVerdict
    baseline_success_rate: float
    ablated_success_rate: float
    success_rate_effect: float
    input_tokens_saved_by_ablation: float
    output_tokens_saved_by_ablation: float
    cost_saved_by_ablation_usd: float
    latency_saved_by_ablation_seconds: float
    quality_per_1k_tokens: float | None
    warnings: tuple[str, ...]
    evidence_scope: EvidenceScope


class PairedAnalyzer:
    """Estimate context effects from matched tasks and repeated trials."""

    def __init__(
        self,
        *,
        confidence: float = 0.95,
        bootstrap_samples: int = 2_000,
        random_seed: int = 0,
        equivalence_tolerance: float = 0.0,
    ) -> None:
        if not 0 < confidence < 1:
            raise ValueError("confidence must be between zero and one")
        if bootstrap_samples < 100:
            raise ValueError("bootstrap_samples must be at least 100")
        if equivalence_tolerance < 0:
            raise ValueError("equivalence_tolerance cannot be negative")
        self.confidence = confidence
        self.bootstrap_samples = bootstrap_samples
        self.random_seed = random_seed
        self.equivalence_tolerance = equivalence_tolerance

    def analyze(
        self,
        measurements: tuple[Measurement, ...],
        *,
        baseline_variant_id: str,
        ablated_variant_id: str,
    ) -> PairedEffect:
        pairs = self._pairs(
            measurements,
            baseline_variant_id,
            ablated_variant_id,
        )
        if not pairs:
            raise ValueError("no matched baseline and ablated measurements")
        baseline = [pair[0] for pair in pairs]
        ablated = [pair[1] for pair in pairs]
        scopes = {
            item.evidence_scope
            for pair in pairs
            for item in pair
        }
        if len(scopes) != 1:
            raise ValueError(
                "screening and target-model measurements cannot be combined"
            )
        evidence_scope = next(iter(scopes))
        differences = [
            baseline_item.score - ablated_item.score
            for baseline_item, ablated_item in pairs
        ]
        score_units = self._score_units(pairs)
        differences = [left - right for left, right in score_units]
        effect = statistics.fmean(differences)
        low, high = self._bootstrap_interval(differences)
        if len(differences) < 2:
            verdict = EffectVerdict.UNCERTAIN
        elif (
            low >= -self.equivalence_tolerance
            and high <= self.equivalence_tolerance
        ):
            verdict = EffectVerdict.NEUTRAL
        elif low > 0:
            verdict = EffectVerdict.HELPFUL
        elif high < 0:
            verdict = EffectVerdict.HARMFUL
        else:
            verdict = EffectVerdict.UNCERTAIN

        baseline_mean = statistics.fmean(left for left, _ in score_units)
        ablated_mean = statistics.fmean(right for _, right in score_units)
        input_saved = statistics.fmean(
            left.input_tokens - right.input_tokens
            for left, right in pairs
        )
        output_saved = statistics.fmean(
            left.output_tokens - right.output_tokens
            for left, right in pairs
        )
        cost_saved = statistics.fmean(
            left.cost_usd - right.cost_usd
            for left, right in pairs
        )
        latency_saved = statistics.fmean(
            left.latency_seconds - right.latency_seconds
            for left, right in pairs
        )
        warnings = self._warnings(baseline, differences)
        relative = effect / abs(ablated_mean) if ablated_mean != 0 else None
        quality_per_tokens = (
            effect / (input_saved / 1000)
            if input_saved != 0
            else None
        )
        baseline_success = statistics.fmean(item.success for item in baseline)
        ablated_success = statistics.fmean(item.success for item in ablated)
        return PairedEffect(
            baseline_variant_id=baseline_variant_id,
            ablated_variant_id=ablated_variant_id,
            pair_count=len(pairs),
            task_count=len({item.task_id for item in baseline}),
            baseline_mean=baseline_mean,
            ablated_mean=ablated_mean,
            effect=effect,
            relative_effect=relative,
            confidence_low=low,
            confidence_high=high,
            verdict=verdict,
            baseline_success_rate=baseline_success,
            ablated_success_rate=ablated_success,
            success_rate_effect=baseline_success - ablated_success,
            input_tokens_saved_by_ablation=input_saved,
            output_tokens_saved_by_ablation=output_saved,
            cost_saved_by_ablation_usd=cost_saved,
            latency_saved_by_ablation_seconds=latency_saved,
            quality_per_1k_tokens=quality_per_tokens,
            warnings=warnings,
            evidence_scope=evidence_scope,
        )

    @staticmethod
    def _pairs(
        measurements: tuple[Measurement, ...],
        baseline_id: str,
        ablated_id: str,
    ) -> tuple[tuple[Measurement, Measurement], ...]:
        baseline: dict[tuple[str, str], Measurement] = {}
        ablated: dict[tuple[str, str], Measurement] = {}
        for item in measurements:
            key = (item.task_id, item.trial_id)
            target = (
                baseline
                if item.variant_id == baseline_id
                else ablated
                if item.variant_id == ablated_id
                else None
            )
            if target is None:
                continue
            if key in target:
                raise ValueError(
                    f"duplicate measurement for {item.variant_id!r} and {key!r}"
                )
            target[key] = item
        keys = sorted(baseline.keys() & ablated.keys())
        return tuple((baseline[key], ablated[key]) for key in keys)

    def _bootstrap_interval(self, differences: list[float]) -> tuple[float, float]:
        if len(differences) == 1:
            return differences[0], differences[0]
        generator = random.Random(self.random_seed)
        means = sorted(
            statistics.fmean(
                generator.choice(differences)
                for _ in differences
            )
            for _ in range(self.bootstrap_samples)
        )
        alpha = (1 - self.confidence) / 2
        low_index = max(0, math.floor(alpha * (len(means) - 1)))
        high_index = min(
            len(means) - 1,
            math.ceil((1 - alpha) * (len(means) - 1)),
        )
        return means[low_index], means[high_index]

    @staticmethod
    def _score_units(
        pairs: tuple[tuple[Measurement, Measurement], ...],
    ) -> list[tuple[float, float]]:
        by_task: dict[str, list[tuple[float, float]]] = {}
        for baseline, ablated in pairs:
            by_task.setdefault(baseline.task_id, []).append(
                (baseline.score, ablated.score)
            )
        if len(by_task) > 1:
            return [
                (
                    statistics.fmean(left for left, _ in values),
                    statistics.fmean(right for _, right in values),
                )
                for _, values in sorted(by_task.items())
            ]
        return [
            (baseline.score, ablated.score)
            for baseline, ablated in pairs
        ]

    @staticmethod
    def _warnings(
        baseline: list[Measurement],
        differences: list[float],
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        if len(differences) < 5:
            warnings.append("fewer than five paired trials; uncertainty is fragile")
        if len({item.score for item in baseline}) > 1:
            baseline_sd = statistics.stdev(item.score for item in baseline)
            if baseline_sd > 0.1:
                warnings.append("baseline scores are unstable across paired trials")
        if len(set(differences)) > 1 and statistics.stdev(differences) > 0.1:
            warnings.append("context effects vary substantially across trials")
        return tuple(warnings)
