"""Construct, screen, and verify combined context configurations."""

from __future__ import annotations

from typing import Protocol

from contextlens.experiments.evaluation import Evaluator
from contextlens.experiments.model import ContextVariant, ReplayStatus
from contextlens.experiments.runner import ReplayCoordinator
from contextlens.experiments.search import GroupDecision, SearchReport
from contextlens.optimization.model import (
    ContextCandidate,
    OptimizationObjective,
    OptimizationPolicy,
    ScreeningResult,
    VerifiedConfiguration,
)
from contextlens.optimization.predictor import ValuePredictor
from contextlens.profiler.model import SourceProfile
from contextlens.trace.model import ContextSource


class FixedAnswerScorer(Protocol):
    """Score a fixed answer under a supplied context without regenerating it."""

    @property
    def scorer_id(self) -> str:
        """Stable scorer and model identity."""

    def score(
        self,
        context: tuple[ContextSource, ...],
        answer: str,
    ) -> float:
        """Return a higher score when context better supports the answer."""


class ContextOptimizer:
    """Turn search evidence into a combined, target-model-verified candidate."""

    def __init__(
        self,
        context: tuple[ContextSource, ...],
        *,
        profiles: tuple[SourceProfile, ...] = (),
        predictor: ValuePredictor | None = None,
    ) -> None:
        if not context:
            raise ValueError("context cannot be empty")
        self.context = context
        self._by_id = {source.source_id: source for source in context}
        if len(self._by_id) != len(context):
            raise ValueError("context source IDs must be unique")
        self._profiles = {profile.source_id: profile for profile in profiles}
        unknown = set(self._profiles) - set(self._by_id)
        if unknown:
            raise ValueError(f"profiles contain unknown source IDs: {unknown}")
        self.predictor = predictor

    def propose(
        self,
        report: SearchReport,
        policy: OptimizationPolicy,
    ) -> ContextCandidate:
        safe = set(report.recommended_removals)
        keep = {
            source_id
            for node in report.nodes
            if node.decision is GroupDecision.KEEP
            for source_id in node.group.source_ids
        }
        unknown = safe - set(self._by_id)
        if unknown:
            raise ValueError(f"search report contains unknown sources: {unknown}")
        predicted: set[str] = set()
        rationale = [
            f"combined {len(safe)} individually verified safe removal(s)"
        ]

        if policy.objective is OptimizationObjective.MAX_QUALITY:
            safe = {
                source_id
                for source_id in safe
                if self._search_delta(report, source_id) > 0
            }
            rationale.append("kept neutral sources unless removal improved quality")

        retained_tokens = self._retained_tokens(safe)
        if (
            policy.objective is OptimizationObjective.TOKEN_BUDGET
            and policy.token_budget is not None
            and retained_tokens > policy.token_budget
        ):
            if self.predictor is None:
                raise ValueError(
                    "a fitted predictor is required to extend removals "
                    "for a token budget"
                )
            predictor = self.predictor
            candidates = [
                source_id
                for source_id in self._by_id
                if source_id not in safe
                and source_id not in keep
                and source_id in self._profiles
            ]
            ranked = sorted(
                candidates,
                key=lambda source_id: (
                    predictor.predict(
                        self._profiles[source_id]
                    ).predicted_effect,
                    -_tokens(self._by_id[source_id]),
                ),
            )
            for source_id in ranked:
                if retained_tokens <= policy.token_budget:
                    break
                safe.add(source_id)
                predicted.add(source_id)
                retained_tokens -= _tokens(self._by_id[source_id])
            if retained_tokens > policy.token_budget:
                raise ValueError("token budget cannot be reached with eligible sources")
            rationale.append(
                "added lowest-predicted-value sources to meet the token budget"
            )

        removed = tuple(
            source.source_id
            for source in self.context
            if source.source_id in safe
        )
        retained = tuple(
            source.source_id
            for source in self.context
            if source.source_id not in safe
        )
        removed_tokens = sum(_tokens(self._by_id[source_id]) for source_id in removed)
        retained_tokens = sum(
            _tokens(self._by_id[source_id])
            for source_id in retained
        )
        return ContextCandidate(
            candidate_id="combined-candidate",
            removed_source_ids=removed,
            retained_source_ids=retained,
            retained_tokens=retained_tokens,
            removed_tokens=removed_tokens,
            individually_verified_removals=tuple(
                source_id
                for source_id in removed
                if source_id not in predicted
            ),
            predicted_removals=tuple(
                source_id
                for source_id in removed
                if source_id in predicted
            ),
            objective=policy.objective,
            rationale=tuple(rationale),
        )

    def screen(
        self,
        candidate: ContextCandidate,
        *,
        answer: str,
        scorer: FixedAnswerScorer,
    ) -> ScreeningResult:
        selected = tuple(
            source
            for source in self.context
            if source.source_id not in candidate.removed_source_ids
        )
        full_score = scorer.score(self.context, answer)
        candidate_score = scorer.score(selected, answer)
        return ScreeningResult(
            candidate_id=candidate.candidate_id,
            full_context_score=full_score,
            candidate_score=candidate_score,
            score_change=candidate_score - full_score,
            scorer_id=scorer.scorer_id,
        )

    def verify(
        self,
        candidate: ContextCandidate,
        *,
        coordinator: ReplayCoordinator,
        evaluator: Evaluator,
        score_name: str,
        baseline_score: float,
        policy: OptimizationPolicy,
        estimated_cost_usd: float | None = None,
        baseline_cost_usd: float | None = None,
        baseline_latency_seconds: float | None = None,
    ) -> VerifiedConfiguration:
        worker_ids = tuple(
            source.source_id
            for source in coordinator.worker.context
        )
        context_ids = tuple(source.source_id for source in self.context)
        if worker_ids != context_ids:
            raise ValueError(
                "optimizer and replay worker must use the same ordered context"
            )
        variant = ContextVariant(
            variant_id=candidate.candidate_id,
            removed_source_ids=frozenset(candidate.removed_source_ids),
            description="verify combined context candidate",
            estimated_cost_usd=estimated_cost_usd,
        )
        replay = coordinator.run((variant,))[0]
        if replay.status not in {ReplayStatus.COMPLETED, ReplayStatus.CACHED}:
            raise RuntimeError(
                f"combined verification failed: {replay.error or replay.status.value}"
            )
        evaluation = evaluator.evaluate(coordinator.worker.task, replay)
        if score_name not in evaluation.scores:
            raise ValueError(f"evaluator did not return score {score_name!r}")
        score = evaluation.scores[score_name]
        direction = 1 if policy.maximize_score else -1
        quality_change = direction * (score - baseline_score)
        reasons: list[str] = []
        if quality_change < -policy.quality_tolerance:
            reasons.append("combined quality fell outside the allowed tolerance")
        outcome = replay.outcome
        if (
            policy.token_budget is not None
            and replay.context_tokens > policy.token_budget
        ):
            reasons.append("candidate exceeded the token budget")
        if (
            policy.max_cost_usd is not None
            and (
                outcome is None
                or outcome.cost_usd is None
                or outcome.cost_usd > policy.max_cost_usd
            )
        ):
            reasons.append("candidate did not satisfy the cost limit")
        if (
            policy.max_latency_seconds is not None
            and replay.duration_seconds > policy.max_latency_seconds
        ):
            reasons.append("candidate exceeded the latency limit")
        baseline_objective, candidate_objective = self._objective_values(
            policy,
            baseline_score=baseline_score,
            candidate_score=score,
            baseline_cost_usd=baseline_cost_usd,
            candidate_cost_usd=outcome.cost_usd if outcome is not None else None,
            baseline_latency_seconds=baseline_latency_seconds,
            candidate_latency_seconds=replay.duration_seconds,
            candidate_tokens=replay.context_tokens,
        )
        improvement = (
            candidate_objective - baseline_objective
            if baseline_objective is not None
            and candidate_objective is not None
            else None
        )
        if baseline_objective is None or candidate_objective is None:
            reasons.append("selected objective could not be calculated")
        elif improvement is not None and improvement < 0:
            reasons.append("candidate did not improve the selected objective")
        return VerifiedConfiguration(
            candidate=candidate,
            accepted=not reasons,
            baseline_score=baseline_score,
            candidate_score=score,
            quality_change=quality_change,
            replay_result=replay,
            evaluation=evaluation,
            rejection_reasons=tuple(reasons),
            baseline_objective_value=baseline_objective,
            candidate_objective_value=candidate_objective,
            objective_improvement=improvement,
        )

    def _retained_tokens(self, removed: set[str]) -> int:
        return sum(
            _tokens(source)
            for source in self.context
            if source.source_id not in removed
        )

    @staticmethod
    def _search_delta(report: SearchReport, source_id: str) -> float:
        deltas = [
            node.quality_delta
            for node in report.nodes
            if node.decision is GroupDecision.REMOVE
            and source_id in node.group.source_ids
            and node.quality_delta is not None
        ]
        return min(deltas, default=0.0)

    def _objective_values(
        self,
        policy: OptimizationPolicy,
        *,
        baseline_score: float,
        candidate_score: float,
        baseline_cost_usd: float | None,
        candidate_cost_usd: float | None,
        baseline_latency_seconds: float | None,
        candidate_latency_seconds: float,
        candidate_tokens: int,
    ) -> tuple[float | None, float | None]:
        direction = 1 if policy.maximize_score else -1
        if policy.objective is OptimizationObjective.MAX_QUALITY:
            return direction * baseline_score, direction * candidate_score
        if policy.objective is OptimizationObjective.MIN_COST:
            return _negative_pair(baseline_cost_usd, candidate_cost_usd)
        if policy.objective is OptimizationObjective.MIN_LATENCY:
            return _negative_pair(
                baseline_latency_seconds,
                candidate_latency_seconds,
            )
        if policy.objective is OptimizationObjective.TOKEN_BUDGET:
            baseline_tokens = sum(_tokens(source) for source in self.context)
            return -float(baseline_tokens), -float(candidate_tokens)
        if baseline_cost_usd is None or candidate_cost_usd in {None, 0}:
            return None, None
        return (
            direction * baseline_score / baseline_cost_usd
            if baseline_cost_usd > 0
            else None,
            direction * candidate_score / candidate_cost_usd,
        )


def _tokens(source: ContextSource) -> int:
    if source.token_count is not None:
        return source.token_count
    if source.content is None:
        return 0
    return (len(source.content.encode("utf-8")) + 3) // 4


def _negative_pair(
    baseline: float | None,
    candidate: float | None,
) -> tuple[float | None, float | None]:
    return (
        -baseline if baseline is not None else None,
        -candidate if candidate is not None else None,
    )
