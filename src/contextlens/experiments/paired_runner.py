"""Repeated paired execution for adaptive context ablation."""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass

from contextlens.analysis.paired import Measurement, PairedAnalyzer, PairedEffect
from contextlens.experiments.evaluation import Evaluation, Evaluator
from contextlens.experiments.model import ContextVariant, ReplayResult, ReplayStatus
from contextlens.experiments.runner import ReplayCoordinator
from contextlens.experiments.search import (
    AdaptiveAblationPlanner,
    ScoreObservation,
    SearchReport,
)


@dataclass(frozen=True, slots=True)
class PairedRunError:
    """A replay or evaluation failure retained by the paired runner."""

    variant_id: str
    trial_id: str
    stage: str
    message: str
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class PairedInvocation:
    """One fresh replay and the logical trial it belongs to."""

    trial_id: str
    variant_id: str
    result: ReplayResult


@dataclass(frozen=True, slots=True)
class PairedAdaptiveSearchRun:
    """Adaptive search evidence produced by repeated paired replays."""

    report: SearchReport
    replay_results: tuple[ReplayResult, ...]
    evaluations: tuple[Evaluation, ...]
    measurements: tuple[Measurement, ...]
    effects: tuple[PairedEffect, ...]
    errors: tuple[PairedRunError, ...]
    invocations: tuple[PairedInvocation, ...] = ()


class PairedAdaptiveSearchRunner:
    """Drive adaptive search with fresh paired trials and bootstrap intervals."""

    def __init__(
        self,
        planner: AdaptiveAblationPlanner,
        coordinator: ReplayCoordinator,
        evaluator: Evaluator,
        *,
        score_name: str,
        trials: int = 3,
        success_name: str = "success",
        analyzer: PairedAnalyzer | None = None,
        on_invocation: Callable[[PairedInvocation], None] | None = None,
    ) -> None:
        if trials < 1:
            raise ValueError("trials must be positive")
        if coordinator.cache is not None:
            raise ValueError(
                "paired adaptive search requires a cache-disabled coordinator"
            )
        planner_ids = tuple(source.source_id for source in planner.context)
        worker_ids = tuple(
            source.source_id for source in coordinator.worker.context
        )
        if planner_ids != worker_ids:
            raise ValueError(
                "planner and replay worker must use the same ordered context"
            )
        self.planner = planner
        self.coordinator = coordinator
        self.evaluator = evaluator
        self.score_name = score_name
        self.trials = trials
        self.success_name = success_name
        self.analyzer = analyzer or PairedAnalyzer(
            equivalence_tolerance=planner.config.quality_tolerance
        )
        self.on_invocation = on_invocation

    def run(self) -> PairedAdaptiveSearchRun:
        """Execute every logical trial through one uncached coordinator call."""

        replay_results: list[ReplayResult] = []
        evaluations: list[Evaluation] = []
        measurements: list[Measurement] = []
        effects: list[PairedEffect] = []
        errors: list[PairedRunError] = []
        invocations: list[PairedInvocation] = []

        baseline_batch = self.planner.next_batch()
        if len(baseline_batch) != 1 or (
            baseline_batch[0].variant_id != self.planner.BASELINE_ID
        ):
            raise ValueError("planner must request exactly one baseline first")
        baseline_variant = baseline_batch[0]
        initial = self._run_trials(
            baseline_variant,
            trial_prefix="initial-baseline",
            replay_results=replay_results,
            evaluations=evaluations,
            measurements=measurements,
            errors=errors,
            invocations=invocations,
        )
        if not initial:
            self.planner.record_error(
                self.planner.BASELINE_ID,
                "all initial baseline trials failed",
            )
            return self._result(
                replay_results,
                evaluations,
                measurements,
                effects,
                errors,
                invocations,
            )
        baseline_score = statistics.fmean(item.score for item in initial)
        self.planner.record(
            self.planner.BASELINE_ID,
            ScoreObservation(score=baseline_score),
        )

        while variants := self.planner.next_batch():
            for variant in variants:
                trial_prefix = variant.variant_id
                paired_baseline = self._run_trials(
                    baseline_variant,
                    trial_prefix=trial_prefix,
                    replay_results=replay_results,
                    evaluations=evaluations,
                    measurements=measurements,
                    errors=errors,
                    invocations=invocations,
                )
                ablated = self._run_trials(
                    variant,
                    trial_prefix=trial_prefix,
                    replay_results=replay_results,
                    evaluations=evaluations,
                    measurements=measurements,
                    errors=errors,
                    invocations=invocations,
                )
                paired_measurements = (*paired_baseline, *ablated)
                try:
                    effect = self.analyzer.analyze(
                        paired_measurements,
                        baseline_variant_id=self.planner.BASELINE_ID,
                        ablated_variant_id=variant.variant_id,
                    )
                except (KeyError, ValueError) as exception:
                    message = f"{type(exception).__name__}: {exception}"
                    errors.append(
                        PairedRunError(
                            variant_id=variant.variant_id,
                            trial_id="*",
                            stage="analysis",
                            message=message,
                        )
                    )
                    self.planner.record_error(variant.variant_id, message)
                    continue
                effects.append(effect)
                uncertainty = _confidence_radius(effect)
                if effect.pair_count == 1:
                    uncertainty = max(
                        uncertainty,
                        abs(effect.effect) + self.planner.config.quality_tolerance,
                    )
                # Anchor the ablated observation to the original planner baseline.
                # This preserves the paired effect while avoiding baseline drift.
                adjusted_score = baseline_score - effect.effect
                self.planner.record(
                    variant.variant_id,
                    ScoreObservation(
                        score=adjusted_score,
                        uncertainty=uncertainty,
                    ),
                )

        return self._result(
            replay_results,
            evaluations,
            measurements,
            effects,
            errors,
            invocations,
        )

    def _run_trials(
        self,
        variant: ContextVariant,
        *,
        trial_prefix: str,
        replay_results: list[ReplayResult],
        evaluations: list[Evaluation],
        measurements: list[Measurement],
        errors: list[PairedRunError],
        invocations: list[PairedInvocation],
    ) -> tuple[Measurement, ...]:
        selected: list[Measurement] = []
        for index in range(1, self.trials + 1):
            trial_id = f"{trial_prefix}:trial-{index}"
            result = self.coordinator.run((variant,))[0]
            replay_results.append(result)
            invocations.append(
                invocation := PairedInvocation(
                    trial_id=trial_id,
                    variant_id=variant.variant_id,
                    result=result,
                )
            )
            if self.on_invocation is not None:
                self.on_invocation(invocation)
            if result.status is not ReplayStatus.COMPLETED:
                errors.append(
                    PairedRunError(
                        variant_id=variant.variant_id,
                        trial_id=trial_id,
                        stage="replay",
                        message=result.error or result.status.value,
                        run_id=result.run_id,
                    )
                )
                continue
            try:
                evaluation = self.evaluator.evaluate(
                    self.coordinator.worker.task,
                    result,
                )
                measurement = Measurement.from_result(
                    result,
                    evaluation,
                    trial_id=trial_id,
                    score_name=self.score_name,
                    success_name=self.success_name,
                )
            except (KeyError, TypeError, ValueError) as exception:
                errors.append(
                    PairedRunError(
                        variant_id=variant.variant_id,
                        trial_id=trial_id,
                        stage="evaluation",
                        message=f"{type(exception).__name__}: {exception}",
                        run_id=result.run_id,
                    )
                )
                continue
            evaluations.append(evaluation)
            measurements.append(measurement)
            selected.append(measurement)
        return tuple(selected)

    def _result(
        self,
        replay_results: list[ReplayResult],
        evaluations: list[Evaluation],
        measurements: list[Measurement],
        effects: list[PairedEffect],
        errors: list[PairedRunError],
        invocations: list[PairedInvocation],
    ) -> PairedAdaptiveSearchRun:
        return PairedAdaptiveSearchRun(
            report=self.planner.report(),
            replay_results=tuple(replay_results),
            evaluations=tuple(evaluations),
            measurements=tuple(measurements),
            effects=tuple(effects),
            errors=tuple(errors),
            invocations=tuple(invocations),
        )


def _confidence_radius(effect: PairedEffect) -> float:
    """Convert a possibly asymmetric confidence interval to a safe radius."""

    return max(
        effect.effect - effect.confidence_low,
        effect.confidence_high - effect.effect,
        0.0,
    )
