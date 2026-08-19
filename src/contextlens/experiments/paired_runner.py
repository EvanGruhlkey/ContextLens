"""Shared paired execution for context experiments and adaptive ablation."""

from __future__ import annotations

import hashlib
import json
import random
import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from contextlens.analysis.paired import Measurement, PairedAnalyzer, PairedEffect
from contextlens.experiments.adapters import AgentAdapter
from contextlens.experiments.evaluation import Evaluation, Evaluator
from contextlens.experiments.model import (
    AgentSettings,
    ContextVariant,
    ReplayResult,
    ReplayStatus,
    ReplayTask,
)
from contextlens.experiments.runner import ReplayCoordinator, ReplayWorker
from contextlens.experiments.search import (
    AdaptiveAblationPlanner,
    ScoreObservation,
    SearchReport,
)
from contextlens.experiments.setup import WorkspacePreparer
from contextlens.experiments.verification import WorkspaceVerifier
from contextlens.experiments.workspace import DirectorySnapshot
from contextlens.trace.model import ContextSource


class TrialClassification(StrEnum):
    """Causal classification for one fresh agent execution."""

    SUCCESS = "success"
    TASK_FAILURE = "task_failure"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


@dataclass(frozen=True, slots=True)
class ExperimentContext:
    """Exact task-effective context supplied to one side of an experiment."""

    sources: tuple[ContextSource, ...]
    provider: str
    target_paths: tuple[str, ...] = ()
    source_paths: tuple[str, ...] = ()
    resolution: tuple[Mapping[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "target_paths", tuple(self.target_paths))
        object.__setattr__(self, "source_paths", tuple(self.source_paths))
        object.__setattr__(
            self,
            "resolution",
            tuple(MappingProxyType(dict(item)) for item in self.resolution),
        )
        object.__setattr__(self, "warnings", tuple(self.warnings))
        if not self.provider:
            raise ValueError("context provider cannot be empty")

    @property
    def content_hash(self) -> str:
        return _stable_hash([source.to_dict() for source in self.sources])

    @property
    def content_hashes(self) -> dict[str, str]:
        return {
            source.source_id: _stable_hash(source.to_dict()) for source in self.sources
        }

    @property
    def effective_tokens(self) -> int:
        return sum(_source_tokens(source) for source in self.sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "target_paths": list(self.target_paths),
            "sources": list(self.source_paths),
            "source_ids": [source.source_id for source in self.sources],
            "effective_tokens": self.effective_tokens,
            "content_hash": self.content_hash,
            "content_hashes": self.content_hashes,
            "scope_resolution": [dict(item) for item in self.resolution],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class ExperimentEvent:
    """Progress event emitted without coupling the engine to a terminal UI."""

    phase: str
    experiment_id: str
    task_id: str
    trial: int
    variant: str
    order_position: int
    classification: TrialClassification | None = None
    result: ReplayResult | None = None


AgentFactory = Callable[[], AgentAdapter]
ProgressCallback = Callable[[ExperimentEvent], None]


@dataclass(frozen=True, slots=True)
class PairedAgentExperiment:
    """One controlled baseline-versus-candidate coding-agent experiment."""

    experiment_id: str
    repository: str
    commit: str
    snapshot: DirectorySnapshot
    task: ReplayTask
    base_context: ExperimentContext
    candidate_context: ExperimentContext
    agent_factory: AgentFactory
    settings: AgentSettings
    evaluator: Evaluator
    trials: int = 3
    timeout_seconds: float = 300.0
    verifier: WorkspaceVerifier | None = None
    preparer: WorkspacePreparer | None = None
    grader_definition: Mapping[str, Any] = field(default_factory=dict)
    pricing_snapshot: Mapping[str, Any] | None = None
    order_seed: int | None = None

    def __post_init__(self) -> None:
        if not self.experiment_id or not self.repository or not self.commit:
            raise ValueError("experiment identity fields cannot be empty")
        if self.trials < 1:
            raise ValueError("experiment trials must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("experiment timeout must be positive")
        object.__setattr__(
            self,
            "grader_definition",
            MappingProxyType(dict(self.grader_definition)),
        )
        if self.pricing_snapshot is not None:
            object.__setattr__(
                self,
                "pricing_snapshot",
                MappingProxyType(dict(self.pricing_snapshot)),
            )


@dataclass(frozen=True, slots=True)
class AgentTrial:
    """Raw evidence from one fresh agent process and isolated workspace."""

    trial: int
    variant: str
    order_position: int
    agent_instance_id: str
    fixed_dimensions_hash: str
    context_hash: str
    classification: TrialClassification
    result: ReplayResult
    evaluation: Evaluation | None
    error_stage: str | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.classification is TrialClassification.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial": self.trial,
            "variant": self.variant,
            "order_position": self.order_position,
            "agent_instance_id": self.agent_instance_id,
            "fixed_dimensions_hash": self.fixed_dimensions_hash,
            "context_hash": self.context_hash,
            "classification": self.classification.value,
            "infrastructure_valid": (
                self.classification is not TrialClassification.INFRASTRUCTURE_ERROR
            ),
            "success": self.success,
            "error_stage": self.error_stage,
            "error": self.error,
            "replay": _replay_to_dict(self.result),
            "evaluation": _evaluation_to_dict(self.evaluation),
        }


@dataclass(frozen=True, slots=True)
class PairedAgentTrial:
    """Stable base/candidate mapping for one logical trial."""

    trial: int
    order: tuple[str, str]
    base: AgentTrial
    candidate: AgentTrial

    @property
    def infrastructure_valid(self) -> bool:
        return all(
            item.classification is not TrialClassification.INFRASTRUCTURE_ERROR
            for item in (self.base, self.candidate)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial": self.trial,
            "order": list(self.order),
            "infrastructure_valid": self.infrastructure_valid,
            "base_run_id": self.base.result.run_id,
            "candidate_run_id": self.candidate.result.run_id,
            "delta": _pair_delta(self.base, self.candidate),
        }


@dataclass(frozen=True, slots=True)
class PairedAgentExperimentRun:
    """Manifest, raw trials, pairing, and execution-level aggregates."""

    manifest: Mapping[str, Any]
    pairs: tuple[PairedAgentTrial, ...]
    invocations: tuple[AgentTrial, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest", MappingProxyType(dict(self.manifest)))

    @property
    def infrastructure_errors(self) -> int:
        return sum(
            item.classification is TrialClassification.INFRASTRUCTURE_ERROR
            for item in self.invocations
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": dict(self.manifest),
            "execution_status": (
                "infrastructure_invalid" if self.infrastructure_errors else "completed"
            ),
            "infrastructure_errors": self.infrastructure_errors,
            "aggregate": {
                "base": _execution_aggregate(
                    tuple(item for item in self.invocations if item.variant == "base")
                ),
                "candidate": _execution_aggregate(
                    tuple(
                        item for item in self.invocations if item.variant == "candidate"
                    )
                ),
            },
            "pairs": [pair.to_dict() for pair in self.pairs],
            "raw_trials": [item.to_dict() for item in self.invocations],
        }


class ContextExperimentRunner:
    """Launch fresh paired agents while holding every non-context input fixed."""

    def __init__(
        self,
        experiment: PairedAgentExperiment,
        *,
        on_event: ProgressCallback | None = None,
    ) -> None:
        self.experiment = experiment
        self.on_event = on_event

    def run(self) -> PairedAgentExperimentRun:
        experiment = self.experiment
        fixed_hash = _fixed_dimensions_hash(experiment)
        invocations: list[AgentTrial] = []
        pairs: list[PairedAgentTrial] = []
        adapter_ids: set[str] = set()
        adapter_versions: set[str] = set()
        randomizer = (
            random.Random(experiment.order_seed)
            if experiment.order_seed is not None
            else None
        )
        for trial in range(1, experiment.trials + 1):
            if randomizer is not None:
                sampled = randomizer.sample(("base", "candidate"), 2)
                order = (sampled[0], sampled[1])
            else:
                order = ("base", "candidate") if trial % 2 else ("candidate", "base")
            selected: dict[str, AgentTrial] = {}
            for order_position, variant in enumerate(order, start=1):
                context = (
                    experiment.base_context
                    if variant == "base"
                    else experiment.candidate_context
                )
                self._emit(
                    ExperimentEvent(
                        phase="starting",
                        experiment_id=experiment.experiment_id,
                        task_id=experiment.task.task_id,
                        trial=trial,
                        variant=variant,
                        order_position=order_position,
                    )
                )
                # Freshness is guaranteed by the experiment engine, independent of
                # whether a particular adapter happens to spawn a new subprocess.
                adapter = experiment.agent_factory()
                adapter_ids.add(adapter.adapter_id)
                if (version := _adapter_version(adapter)) is not None:
                    adapter_versions.add(version)
                if len(adapter_ids) != 1 or len(adapter_versions) > 1:
                    raise ValueError(
                        "agent factory changed adapter identity within a paired "
                        "experiment"
                    )
                worker = ReplayWorker(
                    adapter=adapter,
                    snapshot=experiment.snapshot,
                    task=experiment.task,
                    context=context.sources,
                    settings=experiment.settings,
                    timeout_seconds=experiment.timeout_seconds,
                    verifier=experiment.verifier,
                    preparer=experiment.preparer,
                )
                result = worker.run(
                    ContextVariant(
                        variant,
                        description=f"{variant} task-effective repository context",
                    )
                )
                evaluation: Evaluation | None = None
                error_stage: str | None = None
                error = result.error
                if result.status is ReplayStatus.COMPLETED:
                    try:
                        evaluation = experiment.evaluator.evaluate(
                            experiment.task,
                            result,
                        )
                    except Exception as exception:
                        error_stage = "evaluation"
                        error = f"{type(exception).__name__}: {exception}"
                else:
                    failure_stage = result.metadata.get("failure_stage")
                    error_stage = (
                        str(failure_stage)
                        if failure_stage is not None
                        else "agent_execution"
                    )
                classification = _classify(result, evaluation)
                agent_trial = AgentTrial(
                    trial=trial,
                    variant=variant,
                    order_position=order_position,
                    agent_instance_id=str(uuid4()),
                    fixed_dimensions_hash=fixed_hash,
                    context_hash=context.content_hash,
                    classification=classification,
                    result=result,
                    evaluation=evaluation,
                    error_stage=error_stage,
                    error=error,
                )
                selected[variant] = agent_trial
                invocations.append(agent_trial)
                self._emit(
                    ExperimentEvent(
                        phase="finished",
                        experiment_id=experiment.experiment_id,
                        task_id=experiment.task.task_id,
                        trial=trial,
                        variant=variant,
                        order_position=order_position,
                        classification=classification,
                        result=result,
                    )
                )
            pairs.append(
                PairedAgentTrial(
                    trial=trial,
                    order=order,
                    base=selected["base"],
                    candidate=selected["candidate"],
                )
            )
        manifest = _manifest(
            experiment,
            fixed_hash=fixed_hash,
            adapter_id=next(iter(adapter_ids)),
            adapter_version=(
                next(iter(adapter_versions)) if adapter_versions else None
            ),
            order=tuple(pair.order for pair in pairs),
        )
        return PairedAgentExperimentRun(
            manifest=manifest,
            pairs=tuple(pairs),
            invocations=tuple(invocations),
        )

    def _emit(self, event: ExperimentEvent) -> None:
        if self.on_event is not None:
            self.on_event(event)


def _classify(
    result: ReplayResult,
    evaluation: Evaluation | None,
) -> TrialClassification:
    if result.status is not ReplayStatus.COMPLETED or evaluation is None:
        return TrialClassification.INFRASTRUCTURE_ERROR
    success = bool(
        evaluation.scores.get(
            "success",
            evaluation.success if evaluation.success is not None else False,
        )
    )
    return TrialClassification.SUCCESS if success else TrialClassification.TASK_FAILURE


def _fixed_dimensions_hash(experiment: PairedAgentExperiment) -> str:
    verifier_id = (
        experiment.verifier.verifier_id if experiment.verifier is not None else None
    )
    preparer = (
        {
            "id": experiment.preparer.preparer_id,
            "definition": experiment.preparer.definition,
        }
        if experiment.preparer is not None
        else None
    )
    return _stable_hash(
        {
            "repository_snapshot": experiment.snapshot.digest,
            "excluded_native_context_paths": experiment.snapshot.excluded_paths,
            "task": {
                "id": experiment.task.task_id,
                "instruction": experiment.task.instruction,
                "metadata": dict(experiment.task.metadata),
            },
            "settings": {
                "provider": experiment.settings.provider,
                "model": experiment.settings.model,
                "seed": experiment.settings.seed,
                "temperature": experiment.settings.temperature,
                "tools": experiment.settings.tools,
                "parameters": dict(experiment.settings.parameters),
            },
            "timeout_seconds": experiment.timeout_seconds,
            "evaluator": experiment.evaluator.evaluator_id,
            "verifier": verifier_id,
            "preparer": preparer,
            "grader_hash": (
                _stable_hash(dict(experiment.grader_definition))
                if experiment.grader_definition
                else None
            ),
        }
    )


def _manifest(
    experiment: PairedAgentExperiment,
    *,
    fixed_hash: str,
    adapter_id: str,
    adapter_version: str | None,
    order: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    reasoning = experiment.settings.parameters.get(
        "reasoning_effort",
        experiment.settings.parameters.get("model_reasoning_effort"),
    )
    task_definition = {
        "task_id": experiment.task.task_id,
        "instruction": experiment.task.instruction,
        "metadata": dict(experiment.task.metadata),
    }
    return {
        "schema_version": "1.0",
        "experiment_id": experiment.experiment_id,
        "repository": experiment.repository,
        "commit": experiment.commit,
        "repository_snapshot": {
            "digest": experiment.snapshot.digest,
            "pinned_identity": experiment.snapshot.identity,
            "excluded_paths": list(experiment.snapshot.excluded_paths),
        },
        "task_id": experiment.task.task_id,
        "task_definition_hash": _stable_hash(task_definition),
        "fixed_dimensions_hash": fixed_hash,
        "agent": {
            "adapter": adapter_id,
            "cli_version": adapter_version,
            "provider": experiment.settings.provider,
            "model": experiment.settings.model,
            "reasoning_effort": reasoning,
            "tools": list(experiment.settings.tools),
        },
        "policy": {
            "trials": experiment.trials,
            "timeout_seconds": experiment.timeout_seconds,
            "order": [list(item) for item in order],
            "order_seed": experiment.order_seed,
        },
        "base_context": experiment.base_context.to_dict(),
        "candidate_context": experiment.candidate_context.to_dict(),
        "grader": {
            "definition_hash": (
                _stable_hash(dict(experiment.grader_definition))
                if experiment.grader_definition
                else None
            ),
            "definition": dict(experiment.grader_definition),
        },
        "workspace_setup": (
            {
                "preparer": experiment.preparer.preparer_id,
                "definition": list(experiment.preparer.definition),
            }
            if experiment.preparer is not None
            else None
        ),
        "pricing_snapshot": (
            dict(experiment.pricing_snapshot)
            if experiment.pricing_snapshot is not None
            else None
        ),
    }


def _replay_to_dict(result: ReplayResult) -> dict[str, Any]:
    outcome = result.outcome
    return {
        "run_id": result.run_id,
        "task_id": result.task_id,
        "variant_id": result.variant_id,
        "status": result.status.value,
        "attempt": result.attempt,
        "duration_seconds": result.duration_seconds,
        "context_source_ids": list(result.context_source_ids),
        "context_tokens": result.context_tokens,
        "workspace_id": result.workspace_id,
        "workspace_path": result.workspace_path,
        "started_at": result.started_at,
        "ended_at": result.ended_at,
        "changed_files": [
            {
                "path": change.path,
                "change": change.change,
                "before_digest": change.before_digest,
                "after_digest": change.after_digest,
                "patch": change.patch,
            }
            for change in result.file_changes
        ],
        "error": result.error,
        "metadata": dict(result.metadata),
        "outcome": (
            {
                "output_text": outcome.output_text,
                "commands": list(outcome.commands),
                "test_results": list(outcome.test_results),
                "provider_usage": {
                    "input_tokens": outcome.input_tokens,
                    "cached_input_tokens": outcome.cached_input_tokens,
                    "uncached_input_tokens": _difference(
                        outcome.input_tokens,
                        outcome.cached_input_tokens,
                    ),
                    "cache_write_input_tokens": outcome.metadata.get(
                        "cache_write_input_tokens"
                    ),
                    "output_tokens": outcome.output_tokens,
                    "reasoning_tokens": outcome.metadata.get("reasoning_tokens"),
                },
                "cost_usd": outcome.cost_usd,
                "tool_calls": outcome.tool_calls,
                "retries": outcome.retries,
                "metadata": dict(outcome.metadata),
            }
            if outcome is not None
            else None
        ),
    }


def _evaluation_to_dict(evaluation: Evaluation | None) -> dict[str, Any] | None:
    if evaluation is None:
        return None
    return {
        "scores": dict(evaluation.scores),
        "success": evaluation.success,
        "dimensions": dict(evaluation.dimensions),
        "utility_score": evaluation.utility_score,
        "evidence": list(evaluation.evidence),
        "tokens": evaluation.tokens.to_dict(),
        "runtime_ms": evaluation.runtime_ms,
        "tool_calls": evaluation.tool_calls,
        "retries": evaluation.retries,
        "metadata": dict(evaluation.metadata),
    }


def _pair_delta(base: AgentTrial, candidate: AgentTrial) -> dict[str, Any]:
    base_outcome = base.result.outcome
    candidate_outcome = candidate.result.outcome
    return {
        "quality": _difference(_quality(candidate), _quality(base)),
        "success": int(candidate.success) - int(base.success),
        "effective_context_tokens": candidate.result.context_tokens
        - base.result.context_tokens,
        "provider_input_tokens": _difference(
            candidate_outcome.input_tokens if candidate_outcome is not None else None,
            base_outcome.input_tokens if base_outcome is not None else None,
        ),
        "uncached_input_tokens": _difference(
            _uncached(candidate_outcome),
            _uncached(base_outcome),
        ),
        "output_tokens": _difference(
            candidate_outcome.output_tokens if candidate_outcome is not None else None,
            base_outcome.output_tokens if base_outcome is not None else None,
        ),
        "cost_usd": _difference(
            candidate_outcome.cost_usd if candidate_outcome is not None else None,
            base_outcome.cost_usd if base_outcome is not None else None,
        ),
        "tool_calls": _difference(
            candidate_outcome.tool_calls if candidate_outcome is not None else None,
            base_outcome.tool_calls if base_outcome is not None else None,
        ),
        "latency_seconds": candidate.result.duration_seconds
        - base.result.duration_seconds,
    }


def _execution_aggregate(items: tuple[AgentTrial, ...]) -> dict[str, Any]:
    valid = tuple(
        item
        for item in items
        if item.classification is not TrialClassification.INFRASTRUCTURE_ERROR
    )
    return {
        "planned_runs": len(items),
        "causal_runs": len(valid),
        "infrastructure_errors": len(items) - len(valid),
        "successes": sum(item.success for item in valid),
        "task_failures": sum(
            item.classification is TrialClassification.TASK_FAILURE for item in valid
        ),
        "median_effective_context_tokens": _median_values(
            item.result.context_tokens for item in valid
        ),
        "median_provider_input_tokens": _median_values(
            item.result.outcome.input_tokens
            for item in valid
            if item.result.outcome is not None
        ),
        "median_output_tokens": _median_values(
            item.result.outcome.output_tokens
            for item in valid
            if item.result.outcome is not None
        ),
        "median_latency_seconds": _median_values(
            item.result.duration_seconds for item in valid
        ),
    }


def _quality(trial: AgentTrial) -> float | None:
    if trial.evaluation is None:
        return None
    value = trial.evaluation.scores.get(
        "quality",
        trial.evaluation.scores.get("success"),
    )
    return float(value) if value is not None else None


def _uncached(outcome: Any) -> int | None:
    if outcome is None:
        return None
    value = _difference(outcome.input_tokens, outcome.cached_input_tokens)
    return int(value) if value is not None else None


def _difference(left: Any, right: Any) -> Any:
    if left is None or right is None:
        return None
    return left - right


def _median_values(values: Any) -> float | None:
    present = [float(value) for value in values if value is not None]
    return statistics.median(present) if present else None


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_tokens(source: ContextSource) -> int:
    if source.token_count is not None:
        return source.token_count
    if source.content is None:
        return 0
    return max(1, (len(source.content.encode("utf-8")) + 3) // 4)


def _adapter_version(adapter: AgentAdapter) -> str | None:
    command = getattr(adapter, "command", ())
    if isinstance(command, tuple | list):
        for part in command:
            text = str(part)
            if text.startswith("@openai/codex@"):
                return text.rsplit("@", 1)[-1]
    version = getattr(adapter, "version", None)
    return str(version) if version is not None else None


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
        worker_ids = tuple(source.source_id for source in coordinator.worker.context)
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
