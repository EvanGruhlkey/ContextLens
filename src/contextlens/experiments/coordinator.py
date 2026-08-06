"""Deterministic, budgeted planning for targeted paired experiments."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from contextlens.experiments.mutations import ContextMutation, MutationOperation
from contextlens.profiler.model import SourceProfile, UsageLabel
from contextlens.trace.model import ContextSource, SourceKind


class ExperimentStatus(StrEnum):
    """Persistable experiment and replay lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


_TRANSITIONS = {
    ExperimentStatus.PENDING: {
        ExperimentStatus.RUNNING,
        ExperimentStatus.CANCELLED,
    },
    ExperimentStatus.RUNNING: {
        ExperimentStatus.EVALUATING,
        ExperimentStatus.FAILED,
        ExperimentStatus.CANCELLED,
        ExperimentStatus.TIMED_OUT,
    },
    ExperimentStatus.EVALUATING: {
        ExperimentStatus.COMPLETED,
        ExperimentStatus.FAILED,
        ExperimentStatus.CANCELLED,
        ExperimentStatus.TIMED_OUT,
    },
}


@dataclass(slots=True)
class ExperimentLifecycle:
    """Validate state changes instead of silently skipping job states."""

    status: ExperimentStatus = ExperimentStatus.PENDING

    def transition(self, status: ExperimentStatus) -> None:
        if status not in _TRANSITIONS.get(self.status, set()):
            raise ValueError(
                f"invalid transition: {self.status.value} -> {status.value}"
            )
        self.status = status


@dataclass(frozen=True, slots=True)
class ExperimentCandidate:
    """One-item mutation with explainable priority components."""

    source_id: str
    mutation: ContextMutation
    expected_token_savings: int
    uncertainty: float
    probability_of_meaningful_effect: float
    evaluator_reliability: float
    estimated_replay_cost: float
    priority: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlannedRun:
    """One side of a stable paired comparison."""

    job_id: str
    pair_id: str
    variant: str
    mutation: ContextMutation | None


@dataclass(frozen=True, slots=True)
class PlannedExperiment:
    """Repeated baseline and variant runs for one mutation."""

    experiment_id: str
    candidate: ExperimentCandidate
    runs: tuple[PlannedRun, ...]


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    """Selected work and unspent budget."""

    objective: str
    budget: int
    planned_runs: int
    experiments: tuple[PlannedExperiment, ...]

    @property
    def remaining_budget(self) -> int:
        return self.budget - self.planned_runs


class DeterministicExperimentCoordinator:
    """Rank and schedule one-item paired experiments without an LLM."""

    def __init__(
        self,
        *,
        evaluator_reliability: float = 0.9,
        paired_runs: int = 2,
    ) -> None:
        if not 0 < evaluator_reliability <= 1:
            raise ValueError("evaluator_reliability must be in (0, 1]")
        if paired_runs < 2:
            raise ValueError("at least two paired runs are required")
        self.evaluator_reliability = evaluator_reliability
        self.paired_runs = paired_runs

    def candidates(
        self,
        context: tuple[ContextSource, ...],
        profiles: tuple[SourceProfile, ...],
    ) -> tuple[ExperimentCandidate, ...]:
        sources = {source.source_id: source for source in context}
        if len(sources) != len(context):
            raise ValueError("context source IDs must be unique")
        if {profile.source_id for profile in profiles} != set(sources):
            raise ValueError("profiles must cover every context source exactly once")
        candidates = [
            self._candidate(sources[profile.source_id], profile)
            for profile in profiles
        ]
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    -item.priority,
                    -item.expected_token_savings,
                    item.source_id,
                ),
            )
        )

    def plan(
        self,
        context: tuple[ContextSource, ...],
        profiles: tuple[SourceProfile, ...],
        *,
        experiment_budget: int,
        objective: str = "balanced",
    ) -> ExperimentPlan:
        if experiment_budget < 1:
            raise ValueError("experiment_budget must be positive")
        objective_value = str(objective)
        if objective_value not in {
            "quality",
            "cost_without_regression",
            "latency_without_regression",
            "balanced",
        }:
            raise ValueError(f"unsupported optimization objective: {objective_value}")
        runs_per_experiment = self.paired_runs * 2
        experiment_count = experiment_budget // runs_per_experiment
        chosen = self.candidates(context, profiles)[:experiment_count]
        experiments = tuple(self._schedule(candidate) for candidate in chosen)
        planned = sum(len(experiment.runs) for experiment in experiments)
        return ExperimentPlan(
            objective=objective_value,
            budget=experiment_budget,
            planned_runs=planned,
            experiments=experiments,
        )

    def _candidate(
        self,
        source: ContextSource,
        profile: SourceProfile,
    ) -> ExperimentCandidate:
        mutation = _mutation_for(source, profile)
        target_tokens = mutation.target_tokens or 0
        expected_savings = max(0, profile.token_count - target_tokens)
        uncertainty = {
            UsageLabel.USED: 0.35,
            UsageLabel.UNUSED: 0.65,
            UsageLabel.DUPLICATED: 0.8,
            UsageLabel.UNCERTAIN: 1.0,
        }[profile.label]
        probability = max(
            0.25,
            profile.relevance_score,
            profile.redundancy_score,
            profile.contradiction_score,
        )
        replay_cost = max(1.0, profile.token_count / 1_000)
        priority = (
            expected_savings
            * uncertainty
            * probability
            * self.evaluator_reliability
            / replay_cost
        )
        reasons = (
            f"{profile.token_count} current tokens",
            f"{profile.label.value} passive usage signal",
            f"{profile.redundancy_score:.2f} redundancy score",
            f"{profile.relevance_score:.2f} task relevance score",
            f"{mutation.operation.value} mutation",
        )
        return ExperimentCandidate(
            source_id=source.source_id,
            mutation=mutation,
            expected_token_savings=expected_savings,
            uncertainty=uncertainty,
            probability_of_meaningful_effect=probability,
            evaluator_reliability=self.evaluator_reliability,
            estimated_replay_cost=replay_cost,
            priority=priority,
            reasons=reasons,
        )

    def _schedule(self, candidate: ExperimentCandidate) -> PlannedExperiment:
        digest = hashlib.sha256(
            repr(candidate.mutation.to_dict()).encode("utf-8")
        ).hexdigest()[:16]
        experiment_id = f"experiment-{digest}"
        runs: list[PlannedRun] = []
        for index in range(self.paired_runs):
            pair_id = f"{experiment_id}-pair-{index + 1}"
            runs.extend(
                (
                    PlannedRun(
                        job_id=f"{pair_id}-baseline",
                        pair_id=pair_id,
                        variant="baseline",
                        mutation=None,
                    ),
                    PlannedRun(
                        job_id=f"{pair_id}-variant",
                        pair_id=pair_id,
                        variant="modified",
                        mutation=candidate.mutation,
                    ),
                )
            )
        return PlannedExperiment(
            experiment_id=experiment_id,
            candidate=candidate,
            runs=tuple(runs),
        )


def _mutation_for(
    source: ContextSource,
    profile: SourceProfile,
) -> ContextMutation:
    if source.kind is SourceKind.TOOL_SCHEMA:
        return ContextMutation(MutationOperation.LAZY_LOAD, source.source_id)
    if source.kind in {SourceKind.COMMAND_OUTPUT, SourceKind.TERMINAL_OUTPUT}:
        return ContextMutation(
            MutationOperation.SUMMARIZE,
            source.source_id,
            target_tokens=max(32, min(512, profile.token_count // 4)),
        )
    if source.target_agent_id or source.target_phase:
        return ContextMutation(
            MutationOperation.SCOPE,
            source.source_id,
            target_agent_ids=(
                (source.target_agent_id,) if source.target_agent_id else ()
            ),
            target_phases=((source.target_phase,) if source.target_phase else ()),
        )
    return ContextMutation(MutationOperation.REMOVE, source.source_id)
