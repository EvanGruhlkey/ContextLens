"""Adaptive group ablation planning and execution."""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from contextlens.experiments.evaluation import Evaluation, Evaluator
from contextlens.experiments.model import (
    ContextVariant,
    ReplayResult,
    ReplayStatus,
)
from contextlens.experiments.runner import ReplayCoordinator
from contextlens.profiler.model import SourceProfile, UsageLabel
from contextlens.trace.model import ContextSource


class GroupDecision(StrEnum):
    """Current conclusion for one tested context group."""

    PENDING = "pending"
    REMOVE = "remove"
    KEEP = "keep"
    SPLIT = "split"
    INCONCLUSIVE = "inconclusive"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ScoreObservation:
    """A scalar task-quality observation with optional uncertainty radius."""

    score: float
    uncertainty: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.score):
            raise ValueError("score must be finite")
        if not math.isfinite(self.uncertainty) or self.uncertainty < 0:
            raise ValueError("uncertainty must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """Search behavior and total planning budgets."""

    quality_tolerance: float = 0.0
    max_experiments: int = 25
    batch_size: int = 4
    max_planned_context_tokens: int | None = None
    max_estimated_cost_usd: float | None = None
    estimated_cost_per_1k_tokens: float | None = None
    maximize: bool = True

    def __post_init__(self) -> None:
        if self.quality_tolerance < 0:
            raise ValueError("quality_tolerance cannot be negative")
        if self.max_experiments < 1 or self.batch_size < 1:
            raise ValueError("experiment and batch limits must be positive")
        for name, value in (
            ("max_planned_context_tokens", self.max_planned_context_tokens),
            ("max_estimated_cost_usd", self.max_estimated_cost_usd),
            ("estimated_cost_per_1k_tokens", self.estimated_cost_per_1k_tokens),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if (
            self.max_estimated_cost_usd is not None
            and self.estimated_cost_per_1k_tokens is None
        ):
            raise ValueError(
                "estimated_cost_per_1k_tokens is required with a cost limit"
            )


@dataclass(frozen=True, slots=True)
class AblationGroup:
    """A related set of sources tested as one intervention."""

    group_id: str
    source_ids: tuple[str, ...]
    parent_id: str | None
    depth: int
    removed_tokens: int
    priority: float


@dataclass(slots=True)
class SearchNode:
    """Mutable experimental state retained in the decision tree."""

    group: AblationGroup
    decision: GroupDecision = GroupDecision.PENDING
    variant_id: str | None = None
    observation: ScoreObservation | None = None
    quality_delta: float | None = None
    reason: str = ""
    children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchReport:
    """Terminal or partial adaptive-search evidence."""

    baseline: ScoreObservation | None
    nodes: tuple[SearchNode, ...]
    experiments_planned: int
    planned_context_tokens: int
    estimated_cost_usd: float
    stopping_reason: str

    @property
    def recommended_removals(self) -> tuple[str, ...]:
        source_ids: set[str] = set()
        for node in self.nodes:
            if node.decision is GroupDecision.REMOVE:
                source_ids.update(node.group.source_ids)
        return tuple(sorted(source_ids))


class AdaptiveAblationPlanner:
    """Choose informative context removals and split only harmful groups."""

    BASELINE_ID = "baseline"

    def __init__(
        self,
        context: tuple[ContextSource, ...],
        *,
        config: SearchConfig | None = None,
        profiles: tuple[SourceProfile, ...] = (),
        groups: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        if not context:
            raise ValueError("context cannot be empty")
        ids = [source.source_id for source in context]
        if len(ids) != len(set(ids)):
            raise ValueError("context source IDs must be unique")
        self.context = context
        self.config = config or SearchConfig()
        self._by_id = {source.source_id: source for source in context}
        self._profile_by_id = {profile.source_id: profile for profile in profiles}
        unknown_profiles = set(self._profile_by_id) - set(self._by_id)
        if unknown_profiles:
            raise ValueError(f"profiles contain unknown sources: {unknown_profiles}")
        initial = groups if groups is not None else self._default_groups()
        self._validate_groups(initial)
        self.nodes: dict[str, SearchNode] = {}
        self._queue: list[tuple[float, str]] = []
        for group_id, source_ids in sorted(initial.items()):
            self._add_group(group_id, tuple(sorted(source_ids)), None, 0)
        self.baseline: ScoreObservation | None = None
        self._baseline_planned = False
        self._variant_to_group: dict[str, str] = {}
        self._experiments_planned = 0
        self._planned_context_tokens = 0
        self._estimated_cost = 0.0
        self._stopping_reason = ""

    def next_batch(self) -> tuple[ContextVariant, ...]:
        variants: list[ContextVariant] = []
        if not self._baseline_planned:
            baseline = self._make_variant(self.BASELINE_ID, frozenset())
            if baseline is None:
                self._stopping_reason = "budget_exhausted_before_baseline"
                return ()
            self._baseline_planned = True
            return (baseline,)
        if self.baseline is None:
            return ()

        while self._queue and len(variants) < self.config.batch_size:
            _, group_id = heapq.heappop(self._queue)
            node = self.nodes[group_id]
            if node.decision is not GroupDecision.PENDING or node.variant_id:
                continue
            variant_id = f"ablate:{group_id}"
            variant = self._make_variant(
                variant_id,
                frozenset(node.group.source_ids),
            )
            if variant is None:
                node.decision = GroupDecision.SKIPPED
                node.reason = "planning budget exhausted"
                continue
            node.variant_id = variant_id
            self._variant_to_group[variant_id] = group_id
            variants.append(variant)

        if not variants and not self._queue and not self._has_unresolved_runs():
            self._stopping_reason = self._stopping_reason or "search_complete"
        return tuple(variants)

    def record(
        self,
        variant_id: str,
        observation: ScoreObservation,
    ) -> None:
        if variant_id == self.BASELINE_ID:
            if self.baseline is not None:
                raise ValueError("baseline has already been recorded")
            self.baseline = observation
            return
        if self.baseline is None:
            raise ValueError("record the baseline before ablations")
        group_id = self._variant_to_group.get(variant_id)
        if group_id is None:
            raise ValueError(f"unknown or unplanned variant: {variant_id}")
        node = self.nodes[group_id]
        if node.observation is not None or node.decision is GroupDecision.ERROR:
            raise ValueError(f"variant has already been recorded: {variant_id}")

        direction = 1.0 if self.config.maximize else -1.0
        delta = direction * (observation.score - self.baseline.score)
        uncertainty = observation.uncertainty + self.baseline.uncertainty
        lower = delta - uncertainty
        upper = delta + uncertainty
        node.observation = observation
        node.quality_delta = delta

        if lower >= -self.config.quality_tolerance:
            node.decision = GroupDecision.REMOVE
            node.reason = "removal stayed within the configured quality tolerance"
        elif upper < -self.config.quality_tolerance:
            if len(node.group.source_ids) == 1:
                node.decision = GroupDecision.KEEP
                node.reason = "removing this source caused a material quality loss"
            else:
                node.decision = GroupDecision.SPLIT
                node.reason = "group contains useful context; testing smaller groups"
                node.children = self._split(node.group)
        else:
            node.decision = GroupDecision.INCONCLUSIVE
            node.reason = "uncertainty overlaps the quality tolerance boundary"

    def record_error(self, variant_id: str, reason: str) -> None:
        if variant_id == self.BASELINE_ID:
            self._stopping_reason = "baseline_failed"
            return
        group_id = self._variant_to_group.get(variant_id)
        if group_id is None:
            raise ValueError(f"unknown or unplanned variant: {variant_id}")
        node = self.nodes[group_id]
        node.decision = GroupDecision.ERROR
        node.reason = reason

    def report(self) -> SearchReport:
        reason = self._stopping_reason
        if not reason:
            reason = "awaiting_results" if self._has_unresolved_runs() else "ready"
        return SearchReport(
            baseline=self.baseline,
            nodes=tuple(
                replace(self.nodes[group_id])
                for group_id in sorted(self.nodes)
            ),
            experiments_planned=self._experiments_planned,
            planned_context_tokens=self._planned_context_tokens,
            estimated_cost_usd=self._estimated_cost,
            stopping_reason=reason,
        )

    def _make_variant(
        self,
        variant_id: str,
        removed: frozenset[str],
    ) -> ContextVariant | None:
        if self._experiments_planned >= self.config.max_experiments:
            self._stopping_reason = "experiment_budget_exhausted"
            return None
        selected_tokens = sum(
            _tokens(source)
            for source in self.context
            if source.source_id not in removed
        )
        if (
            self.config.max_planned_context_tokens is not None
            and self._planned_context_tokens + selected_tokens
            > self.config.max_planned_context_tokens
        ):
            self._stopping_reason = "token_budget_exhausted"
            return None
        estimated_cost = (
            selected_tokens
            * (self.config.estimated_cost_per_1k_tokens or 0.0)
            / 1000
        )
        if (
            self.config.max_estimated_cost_usd is not None
            and self._estimated_cost + estimated_cost
            > self.config.max_estimated_cost_usd
        ):
            self._stopping_reason = "cost_budget_exhausted"
            return None
        self._experiments_planned += 1
        self._planned_context_tokens += selected_tokens
        self._estimated_cost += estimated_cost
        return ContextVariant(
            variant_id=variant_id,
            removed_source_ids=removed,
            description=(
                "full-context baseline"
                if not removed
                else f"remove {len(removed)} context source(s)"
            ),
            estimated_cost_usd=estimated_cost,
        )

    def _default_groups(self) -> dict[str, frozenset[str]]:
        groups: dict[str, set[str]] = {}
        for source in self.context:
            groups.setdefault(source.kind.value, set()).add(source.source_id)
        return {
            f"kind:{kind}": frozenset(source_ids)
            for kind, source_ids in groups.items()
        }

    def _validate_groups(self, groups: Mapping[str, frozenset[str]]) -> None:
        seen: set[str] = set()
        for group_id, source_ids in groups.items():
            if not group_id or not source_ids:
                raise ValueError("group IDs and source sets cannot be empty")
            unknown = source_ids - set(self._by_id)
            if unknown:
                raise ValueError(f"group {group_id!r} has unknown sources: {unknown}")
            overlap = seen & source_ids
            if overlap:
                raise ValueError(f"initial groups overlap at sources: {overlap}")
            seen.update(source_ids)
        missing = set(self._by_id) - seen
        if missing:
            raise ValueError(f"initial groups omit sources: {missing}")

    def _add_group(
        self,
        group_id: str,
        source_ids: tuple[str, ...],
        parent_id: str | None,
        depth: int,
    ) -> None:
        removed_tokens = sum(
            _tokens(self._by_id[source_id])
            for source_id in source_ids
        )
        priority = self._priority(source_ids, removed_tokens)
        group = AblationGroup(
            group_id=group_id,
            source_ids=source_ids,
            parent_id=parent_id,
            depth=depth,
            removed_tokens=removed_tokens,
            priority=priority,
        )
        self.nodes[group_id] = SearchNode(group=group)
        heapq.heappush(self._queue, (-priority, group_id))

    def _priority(self, source_ids: tuple[str, ...], tokens: int) -> float:
        weights = {
            UsageLabel.USED: 0.5,
            UsageLabel.UNUSED: 2.0,
            UsageLabel.DUPLICATED: 2.5,
            UsageLabel.UNCERTAIN: 1.0,
        }
        signals = [
            weights[self._profile_by_id[source_id].label]
            for source_id in source_ids
            if source_id in self._profile_by_id
        ]
        suspicion = sum(signals) / len(signals) if signals else 1.0
        information = math.log2(len(source_ids) + 1)
        return max(tokens, 1) * suspicion * information

    def _split(self, group: AblationGroup) -> tuple[str, ...]:
        midpoint = (len(group.source_ids) + 1) // 2
        parts = (group.source_ids[:midpoint], group.source_ids[midpoint:])
        children: list[str] = []
        for index, source_ids in enumerate(parts, start=1):
            if not source_ids:
                continue
            child_id = f"{group.group_id}/{index}"
            self._add_group(child_id, source_ids, group.group_id, group.depth + 1)
            children.append(child_id)
        return tuple(children)

    def _has_unresolved_runs(self) -> bool:
        return any(
            node.variant_id is not None
            and node.observation is None
            and node.decision is GroupDecision.PENDING
            for node in self.nodes.values()
        )


@dataclass(frozen=True, slots=True)
class AdaptiveSearchRun:
    """Combined planner, replay, and evaluation output."""

    report: SearchReport
    replay_results: tuple[ReplayResult, ...]
    evaluations: tuple[Evaluation, ...]


class AdaptiveSearchRunner:
    """Drive a planner using isolated workers and a scalar evaluator score."""

    def __init__(
        self,
        planner: AdaptiveAblationPlanner,
        coordinator: ReplayCoordinator,
        evaluator: Evaluator,
        *,
        score_name: str,
    ) -> None:
        self.planner = planner
        self.coordinator = coordinator
        self.evaluator = evaluator
        self.score_name = score_name
        planner_ids = tuple(source.source_id for source in planner.context)
        worker_ids = tuple(
            source.source_id for source in coordinator.worker.context
        )
        if planner_ids != worker_ids:
            raise ValueError(
                "planner and replay worker must use the same ordered context"
            )

    def run(self) -> AdaptiveSearchRun:
        replay_results: list[ReplayResult] = []
        evaluations: list[Evaluation] = []
        while variants := self.planner.next_batch():
            results = self.coordinator.run(variants)
            replay_results.extend(results)
            for result in results:
                if result.status not in {
                    ReplayStatus.COMPLETED,
                    ReplayStatus.CACHED,
                }:
                    self.planner.record_error(
                        result.variant_id,
                        result.error or result.status.value,
                    )
                    continue
                evaluation = self.evaluator.evaluate(
                    self.coordinator.worker.task,
                    result,
                )
                evaluations.append(evaluation)
                if self.score_name not in evaluation.scores:
                    raise ValueError(
                        f"evaluator did not return score {self.score_name!r}"
                    )
                uncertainty = float(evaluation.metadata.get("uncertainty", 0.0))
                self.planner.record(
                    result.variant_id,
                    ScoreObservation(
                        score=evaluation.scores[self.score_name],
                        uncertainty=uncertainty,
                    ),
                )
        return AdaptiveSearchRun(
            report=self.planner.report(),
            replay_results=tuple(replay_results),
            evaluations=tuple(evaluations),
        )


def _tokens(source: ContextSource) -> int:
    if source.token_count is not None:
        return source.token_count
    if source.content is None:
        return 0
    return (len(source.content.encode("utf-8")) + 3) // 4
