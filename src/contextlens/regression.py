"""Controlled baseline-versus-candidate regression testing for agent context."""

from __future__ import annotations

import json
import statistics
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from contextlens.evaluators import CodingTaskEvaluator, ExactMatchEvaluator
from contextlens.experiments import (
    AgentSettings,
    CodexCliAgentAdapter,
    CommandWorkspaceVerifier,
    ContextVariant,
    DirectorySnapshot,
    Evaluation,
    ReplayResult,
    ReplayStatus,
    ReplayTask,
    ReplayWorker,
    SubprocessAgentAdapter,
    WorkspaceVerification,
)
from contextlens.repository import (
    diff_repository,
)
from contextlens.telemetry import (
    NormalizedUsage,
    PricingSnapshot,
    calculate_usage_cost,
    usage_from_outcome,
)
from contextlens.trace import ContextSource


class RegressionVerdict(StrEnum):
    """Ship decision for a measured context change."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "CONTEXT REGRESSION"
    INCONCLUSIVE = "INCONCLUSIVE"


class AgentFactory(Protocol):
    """Produce a fresh or safely reusable adapter for a replay."""

    def __call__(self) -> Any:
        """Return an object implementing the AgentAdapter protocol."""


@dataclass(frozen=True, slots=True)
class VerificationTask:
    """One fixed task and mechanical evaluation definition."""

    task_id: str
    instruction: str
    workspace: Path
    checks: tuple[tuple[str, ...], ...] = ()
    expected_output: str | None = None
    allowed_files: tuple[str, ...] = ()
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not self.task_id or not self.instruction:
            raise ValueError("verification task ID and instruction cannot be empty")
        if not self.workspace.is_dir():
            raise ValueError(f"task workspace is not a directory: {self.workspace}")
        if not self.checks and self.expected_output is None:
            raise ValueError(
                f"task {self.task_id!r} needs mechanical checks or expected_output"
            )
        if self.timeout_seconds <= 0:
            raise ValueError("task timeout must be positive")


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    """Fail-closed quality and economics thresholds."""

    trials: int = 3
    quality_tolerance: float = 0.0
    economics_tolerance: float = 0.0
    require_provider_usage: bool = False

    def __post_init__(self) -> None:
        if self.trials < 1:
            raise ValueError("verification trials must be positive")
        if self.quality_tolerance < 0 or self.economics_tolerance < 0:
            raise ValueError("verification tolerances cannot be negative")


@dataclass(frozen=True, slots=True)
class TrialMetrics:
    """Normalized evidence from one isolated agent run."""

    task_id: str
    trial: int
    variant: str
    status: str
    score: float
    success: bool
    initial_context_tokens: int
    provider_usage: NormalizedUsage
    estimated_cost_usd: float | None
    latency_seconds: float
    model_latency_seconds: float | None
    tool_latency_seconds: float | None
    turns: int | None
    tool_calls: int
    files_read: int | None
    searches: int | None
    commands: int
    exploration_breadth: int | None
    retries: int
    changed_files: tuple[str, ...]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "trial": self.trial,
            "variant": self.variant,
            "status": self.status,
            "score": self.score,
            "success": self.success,
            "quality": {
                "score": self.score,
                "success": self.success,
            },
            "economics": {
                "initial_context_tokens": self.initial_context_tokens,
                **self.provider_usage.to_dict(),
                "estimated_cost_usd": self.estimated_cost_usd,
            },
            "behavior": {
                "turns": self.turns,
                "tool_calls": self.tool_calls,
                "files_read": self.files_read,
                "searches": self.searches,
                "commands": self.commands,
                "exploration_breadth": self.exploration_breadth,
                "retries": self.retries,
            },
            "performance": {
                "latency_seconds": self.latency_seconds,
                "model_latency_seconds": self.model_latency_seconds,
                "tool_latency_seconds": self.tool_latency_seconds,
            },
            "changed_files": list(self.changed_files),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class AggregateMetrics:
    """Median resource metrics and mean quality for one context version."""

    runs: int
    completed: int
    successes: int
    success_rate: float
    mean_score: float
    initial_context_tokens: float | None
    provider_input_tokens: float | None
    cached_input_tokens: float | None
    uncached_input_tokens: float | None
    cache_write_input_tokens: float | None
    output_tokens: float | None
    reasoning_tokens: float | None
    estimated_cost_usd: float | None
    latency_seconds: float | None
    model_latency_seconds: float | None
    tool_latency_seconds: float | None
    turns: float | None
    tool_calls: float | None
    files_read: float | None
    searches: float | None
    commands: float | None
    exploration_breadth: float | None
    retries: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "quality": {
                "runs": self.runs,
                "completed": self.completed,
                "successes": self.successes,
                "success_rate": self.success_rate,
                "mean_score": self.mean_score,
            },
            "economics": {
                "initial_context_tokens": self.initial_context_tokens,
                "provider_input_tokens": self.provider_input_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "uncached_input_tokens": self.uncached_input_tokens,
                "cache_write_input_tokens": self.cache_write_input_tokens,
                "output_tokens": self.output_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "estimated_cost_usd": self.estimated_cost_usd,
            },
            "behavior": {
                "turns": self.turns,
                "tool_calls": self.tool_calls,
                "files_read": self.files_read,
                "searches": self.searches,
                "commands": self.commands,
                "exploration_breadth": self.exploration_breadth,
                "retries": self.retries,
            },
            "performance": {
                "latency_seconds": self.latency_seconds,
                "model_latency_seconds": self.model_latency_seconds,
                "tool_latency_seconds": self.tool_latency_seconds,
            },
        }


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """Auditable context-regression result."""

    base_ref: str
    agent_provider: str
    agent_model: str
    policy: VerificationPolicy
    verdict: RegressionVerdict
    rationale: str
    base: AggregateMetrics
    candidate: AggregateMetrics
    trials: tuple[TrialMetrics, ...]
    warnings: tuple[str, ...]
    catastrophic_regressions: int
    paired_runs: int

    @property
    def exit_code(self) -> int:
        return {
            RegressionVerdict.PASS: 0,
            RegressionVerdict.WARN: 0,
            RegressionVerdict.FAIL: 4,
            RegressionVerdict.INCONCLUSIVE: 5,
        }[self.verdict]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "report_type": "context_regression_verification",
            "evidence": "verified/target_model",
            "base_ref": self.base_ref,
            "agent": {
                "provider": self.agent_provider,
                "model": self.agent_model,
            },
            "policy": {
                "trials": self.policy.trials,
                "quality_tolerance": self.policy.quality_tolerance,
                "economics_tolerance": self.policy.economics_tolerance,
                "require_provider_usage": self.policy.require_provider_usage,
            },
            "verdict": self.verdict.value,
            "rationale": self.rationale,
            "catastrophic_regressions": self.catastrophic_regressions,
            "paired_runs": self.paired_runs,
            "base": self.base.to_dict(),
            "candidate": self.candidate.to_dict(),
            "delta": _aggregate_delta(self.base, self.candidate),
            "trials": [trial.to_dict() for trial in self.trials],
            "warnings": list(self.warnings),
        }


class MultiCommandWorkspaceVerifier:
    """Execute multiple mechanical checks without invoking a shell."""

    def __init__(
        self,
        commands: tuple[tuple[str, ...], ...],
        *,
        timeout_seconds: float,
    ) -> None:
        if not commands or any(not command for command in commands):
            raise ValueError("mechanical check commands cannot be empty")
        self.commands = commands
        self.timeout_seconds = timeout_seconds

    @property
    def verifier_id(self) -> str:
        return "multi-command-workspace-verifier-v1"

    def verify(
        self,
        workspace: Path,
        task: ReplayTask,
        outcome: Any,
    ) -> WorkspaceVerification:
        del task, outcome
        started = time.monotonic()
        stdout: list[str] = []
        stderr: list[str] = []
        for command in self.commands:
            remaining = self.timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                return WorkspaceVerification(
                    command=("contextlens-multi-check",),
                    exit_code=None,
                    stdout="\n".join(stdout),
                    stderr="\n".join(stderr),
                    duration_seconds=time.monotonic() - started,
                    timed_out=True,
                )
            try:
                completed = subprocess.run(
                    command,
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=remaining,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                return WorkspaceVerification(
                    command=command,
                    exit_code=None,
                    stdout="\n".join((*stdout, _text(error.stdout))),
                    stderr="\n".join((*stderr, _text(error.stderr))),
                    duration_seconds=time.monotonic() - started,
                    timed_out=True,
                )
            stdout.append(f"$ {' '.join(command)}\n{completed.stdout}")
            stderr.append(completed.stderr)
            if completed.returncode != 0:
                return WorkspaceVerification(
                    command=command,
                    exit_code=completed.returncode,
                    stdout="\n".join(stdout),
                    stderr="\n".join(stderr),
                    duration_seconds=time.monotonic() - started,
                )
        return WorkspaceVerification(
            command=("contextlens-multi-check",),
            exit_code=0,
            stdout="\n".join(stdout),
            stderr="\n".join(stderr),
            duration_seconds=time.monotonic() - started,
        )


def verify_repository(
    config_path: Path,
    *,
    root: Path = Path("."),
    base_ref: str | None = None,
) -> VerificationReport:
    """Load a small checked-in task suite and compare Git base to worktree."""

    resolved_config = config_path.resolve()
    value = json.loads(resolved_config.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("verification config must contain a JSON object")
    resolved_root = root.resolve()
    context_diff = diff_repository(
        resolved_root, base_ref=base_ref or _string(value.get("base_ref"))
    )
    tasks = _tasks_from_config(value, resolved_root)
    agent_value = _mapping(value.get("agent"), "agent")
    provider = _required_string(agent_value, "provider")
    model = _required_string(agent_value, "model")
    settings = AgentSettings(
        provider=provider,
        model=model,
        seed=_optional_int(agent_value.get("seed")),
        temperature=_optional_float(agent_value.get("temperature")),
        tools=tuple(str(item) for item in _sequence(agent_value.get("tools", ()))),
        parameters=_mapping_or_empty(agent_value.get("parameters")),
    )
    policy = VerificationPolicy(
        trials=int(value.get("trials", 3)),
        quality_tolerance=float(value.get("quality_tolerance", 0.0)),
        economics_tolerance=float(value.get("economics_tolerance", 0.0)),
        require_provider_usage=bool(value.get("require_provider_usage", False)),
    )
    maximum_runs = int(value.get("max_runs", len(tasks) * policy.trials * 2))
    required_runs = len(tasks) * policy.trials * 2
    if required_runs > maximum_runs:
        raise ValueError(
            f"verification requires {required_runs} runs, "
            f"exceeding max_runs={maximum_runs}"
        )
    pricing_value = value.get("pricing")
    pricing = (
        PricingSnapshot.from_dict(_mapping(pricing_value, "pricing"))
        if pricing_value is not None
        else None
    )
    factory = _agent_factory(agent_value)
    return run_context_verification(
        base_context=context_diff.base.to_context_sources(),
        candidate_context=context_diff.candidate.to_context_sources(),
        tasks=tasks,
        agent_factory=factory,
        settings=settings,
        policy=policy,
        base_ref=context_diff.base_ref,
        pricing=pricing,
    )


def verify_context_candidate(
    config_path: Path,
    *,
    base_context: tuple[ContextSource, ...],
    candidate_context: tuple[ContextSource, ...],
    root: Path = Path("."),
    base_label: str = "current",
) -> VerificationReport:
    """Verify an in-memory minimization candidate with the checked-in suite."""

    value = json.loads(config_path.resolve().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("verification config must contain a JSON object")
    resolved_root = root.resolve()
    tasks = _tasks_from_config(value, resolved_root)
    agent_value = _mapping(value.get("agent"), "agent")
    settings = AgentSettings(
        provider=_required_string(agent_value, "provider"),
        model=_required_string(agent_value, "model"),
        seed=_optional_int(agent_value.get("seed")),
        temperature=_optional_float(agent_value.get("temperature")),
        tools=tuple(str(item) for item in _sequence(agent_value.get("tools", ()))),
        parameters=_mapping_or_empty(agent_value.get("parameters")),
    )
    policy = VerificationPolicy(
        trials=int(value.get("trials", 3)),
        quality_tolerance=float(value.get("quality_tolerance", 0.0)),
        economics_tolerance=float(value.get("economics_tolerance", 0.0)),
        require_provider_usage=bool(value.get("require_provider_usage", False)),
    )
    pricing_value = value.get("pricing")
    pricing = (
        PricingSnapshot.from_dict(_mapping(pricing_value, "pricing"))
        if pricing_value is not None
        else None
    )
    return run_context_verification(
        base_context=base_context,
        candidate_context=candidate_context,
        tasks=tasks,
        agent_factory=_agent_factory(agent_value),
        settings=settings,
        policy=policy,
        base_ref=base_label,
        pricing=pricing,
    )


def run_context_verification(
    *,
    base_context: tuple[ContextSource, ...],
    candidate_context: tuple[ContextSource, ...],
    tasks: tuple[VerificationTask, ...],
    agent_factory: AgentFactory,
    settings: AgentSettings,
    policy: VerificationPolicy | None = None,
    base_ref: str = "base",
    pricing: PricingSnapshot | None = None,
) -> VerificationReport:
    """Run fresh matched trials while changing only supplied context."""

    if not tasks:
        raise ValueError("verification requires at least one task")
    selected_policy = policy or VerificationPolicy()
    trial_metrics: list[TrialMetrics] = []
    for task in tasks:
        replay_task = ReplayTask(
            task.task_id,
            task.instruction,
            metadata={"allowed_files": list(task.allowed_files)},
        )
        verifier = _verifier(task)
        workers = {
            "base": ReplayWorker(
                adapter=agent_factory(),
                snapshot=DirectorySnapshot(task.workspace),
                task=replay_task,
                context=base_context,
                settings=settings,
                timeout_seconds=task.timeout_seconds,
                verifier=verifier,
            ),
            "candidate": ReplayWorker(
                adapter=agent_factory(),
                snapshot=DirectorySnapshot(task.workspace),
                task=replay_task,
                context=candidate_context,
                settings=settings,
                timeout_seconds=task.timeout_seconds,
                verifier=verifier,
            ),
        }
        evaluator = (
            ExactMatchEvaluator({task.task_id: task.expected_output})
            if task.expected_output is not None
            else CodingTaskEvaluator(objective="quality")
        )
        for trial in range(1, selected_policy.trials + 1):
            order = ("base", "candidate") if trial % 2 else ("candidate", "base")
            for variant in order:
                result = workers[variant].run(
                    ContextVariant(
                        variant,
                        description=f"{variant} repository context",
                    )
                )
                evaluation = evaluator.evaluate(replay_task, result)
                trial_metrics.append(
                    _trial_metrics(
                        result,
                        evaluation,
                        trial=trial,
                        variant=variant,
                        pricing=pricing,
                    )
                )
    base = _aggregate(tuple(item for item in trial_metrics if item.variant == "base"))
    candidate = _aggregate(
        tuple(item for item in trial_metrics if item.variant == "candidate")
    )
    paired = _paired(trial_metrics)
    catastrophic = sum(
        base_item.success and not candidate_item.success
        for base_item, candidate_item in paired
    )
    verdict, rationale, warnings = _verdict(
        base,
        candidate,
        paired_runs=len(paired),
        catastrophic_regressions=catastrophic,
        policy=selected_policy,
    )
    return VerificationReport(
        base_ref=base_ref,
        agent_provider=settings.provider,
        agent_model=settings.model,
        policy=selected_policy,
        verdict=verdict,
        rationale=rationale,
        base=base,
        candidate=candidate,
        trials=tuple(trial_metrics),
        warnings=warnings,
        catastrophic_regressions=catastrophic,
        paired_runs=len(paired),
    )


def render_verification_terminal(report: VerificationReport) -> str:
    """Render the product-level A/B answer engineering teams need."""

    base = report.base
    candidate = report.candidate
    rows = (
        (
            "Success",
            f"{base.successes}/{base.runs}",
            f"{candidate.successes}/{candidate.runs}",
            _rate_delta(base.success_rate, candidate.success_rate),
        ),
        (
            "Mean quality",
            _number(base.mean_score),
            _number(candidate.mean_score),
            _delta(base.mean_score, candidate.mean_score),
        ),
        (
            "Initial context",
            _tokens(base.initial_context_tokens),
            _tokens(candidate.initial_context_tokens),
            _percent_delta(
                base.initial_context_tokens, candidate.initial_context_tokens
            ),
        ),
        (
            "Provider input",
            _tokens(base.provider_input_tokens),
            _tokens(candidate.provider_input_tokens),
            _percent_delta(base.provider_input_tokens, candidate.provider_input_tokens),
        ),
        (
            "Cached input",
            _tokens(base.cached_input_tokens),
            _tokens(candidate.cached_input_tokens),
            _percent_delta(base.cached_input_tokens, candidate.cached_input_tokens),
        ),
        (
            "Uncached input",
            _tokens(base.uncached_input_tokens),
            _tokens(candidate.uncached_input_tokens),
            _percent_delta(base.uncached_input_tokens, candidate.uncached_input_tokens),
        ),
        (
            "Output",
            _tokens(base.output_tokens),
            _tokens(candidate.output_tokens),
            _percent_delta(base.output_tokens, candidate.output_tokens),
        ),
        (
            "Reasoning",
            _tokens(base.reasoning_tokens),
            _tokens(candidate.reasoning_tokens),
            _percent_delta(base.reasoning_tokens, candidate.reasoning_tokens),
        ),
        (
            "Agent turns",
            _number(base.turns),
            _number(candidate.turns),
            _percent_delta(base.turns, candidate.turns),
        ),
        (
            "Tool calls",
            _number(base.tool_calls),
            _number(candidate.tool_calls),
            _percent_delta(base.tool_calls, candidate.tool_calls),
        ),
        (
            "Files read",
            _number(base.files_read),
            _number(candidate.files_read),
            _percent_delta(base.files_read, candidate.files_read),
        ),
        (
            "Searches",
            _number(base.searches),
            _number(candidate.searches),
            _percent_delta(base.searches, candidate.searches),
        ),
        (
            "Median latency",
            _seconds(base.latency_seconds),
            _seconds(candidate.latency_seconds),
            _percent_delta(base.latency_seconds, candidate.latency_seconds),
        ),
        (
            "Estimated cost",
            _money(base.estimated_cost_usd),
            _money(candidate.estimated_cost_usd),
            _percent_delta(base.estimated_cost_usd, candidate.estimated_cost_usd),
        ),
    )
    widths = (22, 14, 14, 12)
    lines = [
        "ContextLens Verify",
        "",
        (
            f"{'Metric':{widths[0]}} {'base':>{widths[1]}} "
            f"{'candidate':>{widths[2]}} {'delta':>{widths[3]}}"
        ),
        "-" * sum(widths),
    ]
    lines.extend(
        (
            f"{name:{widths[0]}} {left:>{widths[1]}} "
            f"{right:>{widths[2]}} {delta:>{widths[3]}}"
        )
        for name, left, right, delta in rows
    )
    lines.extend(("", f"VERDICT: {report.verdict.value}", "", report.rationale))
    if report.warnings:
        lines.extend(("", "Warnings"))
        lines.extend(f"- {warning}" for warning in report.warnings)
    return "\n".join(lines) + "\n"


def render_verification_markdown(report: VerificationReport) -> str:
    """Render a PR-comment/GitHub-step-summary compatible result."""

    base = report.base
    candidate = report.candidate
    rows = (
        (
            "Success",
            f"{base.successes}/{base.runs}",
            f"{candidate.successes}/{candidate.runs}",
            _rate_delta(base.success_rate, candidate.success_rate),
        ),
        (
            "Initial context",
            _tokens(base.initial_context_tokens),
            _tokens(candidate.initial_context_tokens),
            _percent_delta(
                base.initial_context_tokens, candidate.initial_context_tokens
            ),
        ),
        (
            "Provider input",
            _tokens(base.provider_input_tokens),
            _tokens(candidate.provider_input_tokens),
            _percent_delta(base.provider_input_tokens, candidate.provider_input_tokens),
        ),
        (
            "Cached input",
            _tokens(base.cached_input_tokens),
            _tokens(candidate.cached_input_tokens),
            _percent_delta(base.cached_input_tokens, candidate.cached_input_tokens),
        ),
        (
            "Uncached input",
            _tokens(base.uncached_input_tokens),
            _tokens(candidate.uncached_input_tokens),
            _percent_delta(base.uncached_input_tokens, candidate.uncached_input_tokens),
        ),
        (
            "Output tokens",
            _tokens(base.output_tokens),
            _tokens(candidate.output_tokens),
            _percent_delta(base.output_tokens, candidate.output_tokens),
        ),
        (
            "Reasoning tokens",
            _tokens(base.reasoning_tokens),
            _tokens(candidate.reasoning_tokens),
            _percent_delta(base.reasoning_tokens, candidate.reasoning_tokens),
        ),
        (
            "Tool calls",
            _number(base.tool_calls),
            _number(candidate.tool_calls),
            _percent_delta(base.tool_calls, candidate.tool_calls),
        ),
        (
            "Median latency",
            _seconds(base.latency_seconds),
            _seconds(candidate.latency_seconds),
            _percent_delta(base.latency_seconds, candidate.latency_seconds),
        ),
        (
            "Estimated cost",
            _money(base.estimated_cost_usd),
            _money(candidate.estimated_cost_usd),
            _percent_delta(base.estimated_cost_usd, candidate.estimated_cost_usd),
        ),
    )
    body = "\n".join(
        f"| {name} | {left} | {right} | {delta} |" for name, left, right, delta in rows
    )
    warnings = "\n".join(f"- {warning}" for warning in report.warnings)
    return (
        "## ContextLens — Agent Context Regression\n\n"
        f"**RESULT: {report.verdict.value}**\n\n"
        "| Metric | Base | Candidate | Delta |\n"
        "| --- | ---: | ---: | ---: |\n"
        f"{body}\n\n"
        f"{report.rationale}\n"
        + (f"\n### Warnings\n\n{warnings}\n" if warnings else "")
    )


def _trial_metrics(
    result: ReplayResult,
    evaluation: Evaluation,
    *,
    trial: int,
    variant: str,
    pricing: PricingSnapshot | None,
) -> TrialMetrics:
    outcome = result.outcome
    usage = usage_from_outcome(outcome)
    calculated = calculate_usage_cost(usage, pricing) if pricing is not None else None
    cost = (
        outcome.cost_usd
        if outcome is not None and outcome.cost_usd is not None
        else calculated.total_usd
        if calculated is not None
        else None
    )
    metadata = dict(outcome.metadata) if outcome is not None else {}
    success = bool(evaluation.scores.get("success", evaluation.success or False))
    score = float(
        evaluation.scores.get(
            "quality",
            evaluation.scores.get("exact_match", evaluation.scores.get("success", 0.0)),
        )
    )
    files_read = _count(metadata.get("files_read"))
    searches = _count(metadata.get("searches", metadata.get("search_queries")))
    turns = _optional_int(metadata.get("turns", metadata.get("inference_turns")))
    exploration = _optional_int(metadata.get("exploration_breadth"))
    if exploration is None and files_read is not None:
        exploration = files_read
    return TrialMetrics(
        task_id=result.task_id,
        trial=trial,
        variant=variant,
        status=result.status.value,
        score=score,
        success=success,
        initial_context_tokens=result.context_tokens,
        provider_usage=usage,
        estimated_cost_usd=cost,
        latency_seconds=result.duration_seconds,
        model_latency_seconds=_milliseconds_to_seconds(
            metadata.get("model_latency_ms")
        ),
        tool_latency_seconds=_milliseconds_to_seconds(metadata.get("tool_latency_ms")),
        turns=turns,
        tool_calls=outcome.tool_calls if outcome is not None else 0,
        files_read=files_read,
        searches=searches,
        commands=len(outcome.commands) if outcome is not None else 0,
        exploration_breadth=exploration,
        retries=max(outcome.retries if outcome is not None else 0, result.attempt - 1),
        changed_files=tuple(change.path for change in result.file_changes),
        error=result.error,
    )


def _aggregate(items: tuple[TrialMetrics, ...]) -> AggregateMetrics:
    if not items:
        raise ValueError("cannot aggregate an empty set of verification trials")
    return AggregateMetrics(
        runs=len(items),
        completed=sum(
            item.status in {ReplayStatus.COMPLETED.value, ReplayStatus.CACHED.value}
            for item in items
        ),
        successes=sum(item.success for item in items),
        success_rate=statistics.fmean(item.success for item in items),
        mean_score=statistics.fmean(item.score for item in items),
        initial_context_tokens=_median(item.initial_context_tokens for item in items),
        provider_input_tokens=_median(
            item.provider_usage.input_tokens for item in items
        ),
        cached_input_tokens=_median(
            item.provider_usage.cached_input_tokens for item in items
        ),
        uncached_input_tokens=_median(
            item.provider_usage.uncached_input_tokens for item in items
        ),
        cache_write_input_tokens=_median(
            item.provider_usage.cache_write_input_tokens for item in items
        ),
        output_tokens=_median(item.provider_usage.output_tokens for item in items),
        reasoning_tokens=_median(
            item.provider_usage.reasoning_tokens for item in items
        ),
        estimated_cost_usd=_median(item.estimated_cost_usd for item in items),
        latency_seconds=_median(item.latency_seconds for item in items),
        model_latency_seconds=_median(item.model_latency_seconds for item in items),
        tool_latency_seconds=_median(item.tool_latency_seconds for item in items),
        turns=_median(item.turns for item in items),
        tool_calls=_median(item.tool_calls for item in items),
        files_read=_median(item.files_read for item in items),
        searches=_median(item.searches for item in items),
        commands=_median(item.commands for item in items),
        exploration_breadth=_median(item.exploration_breadth for item in items),
        retries=_median(item.retries for item in items),
    )


def _paired(
    items: Sequence[TrialMetrics],
) -> tuple[tuple[TrialMetrics, TrialMetrics], ...]:
    base = {
        (item.task_id, item.trial): item for item in items if item.variant == "base"
    }
    candidate = {
        (item.task_id, item.trial): item
        for item in items
        if item.variant == "candidate"
    }
    return tuple(
        (base[key], candidate[key]) for key in sorted(base.keys() & candidate.keys())
    )


def _verdict(
    base: AggregateMetrics,
    candidate: AggregateMetrics,
    *,
    paired_runs: int,
    catastrophic_regressions: int,
    policy: VerificationPolicy,
) -> tuple[RegressionVerdict, str, tuple[str, ...]]:
    warnings: list[str] = []
    if paired_runs == 0:
        return (
            RegressionVerdict.INCONCLUSIVE,
            "No matched baseline/candidate runs completed.",
            (),
        )
    if catastrophic_regressions:
        return (
            RegressionVerdict.FAIL,
            (
                f"Candidate failed {catastrophic_regressions} task trial(s) "
                "that passed with base context."
            ),
            (),
        )
    if candidate.completed < candidate.runs:
        return (
            RegressionVerdict.FAIL,
            "At least one candidate replay failed; verification fails closed.",
            (),
        )
    if candidate.success_rate < base.success_rate - policy.quality_tolerance:
        return (
            RegressionVerdict.FAIL,
            "Candidate task success regressed beyond the configured tolerance.",
            (),
        )
    if candidate.mean_score < base.mean_score - policy.quality_tolerance:
        return (
            RegressionVerdict.FAIL,
            "Candidate quality regressed beyond the configured tolerance.",
            (),
        )
    if paired_runs < 2:
        warnings.append(
            "only one matched pair; nondeterministic effects are not resolved"
        )
        return (
            RegressionVerdict.INCONCLUSIVE,
            (
                "Quality did not regress in the observed pair, but evidence "
                "is insufficient."
            ),
            tuple(warnings),
        )

    base_cost = base.estimated_cost_usd
    candidate_cost = candidate.estimated_cost_usd
    if (
        base_cost is not None
        and candidate_cost is not None
        and _regressed(base_cost, candidate_cost, policy.economics_tolerance)
    ):
        return (
            RegressionVerdict.FAIL,
            (
                "Candidate preserved quality but increased estimated "
                "end-to-end model cost."
            ),
            (),
        )
    if (
        base.provider_input_tokens is not None
        and candidate.provider_input_tokens is not None
        and _regressed(
            base.provider_input_tokens,
            candidate.provider_input_tokens,
            policy.economics_tolerance,
        )
        and candidate.mean_score <= base.mean_score + policy.quality_tolerance
    ):
        return (
            RegressionVerdict.FAIL,
            (
                "Initial context did not buy a quality improvement and total "
                "provider input increased, indicating compensating exploration "
                "or cache loss."
            ),
            (),
        )
    if (
        base.uncached_input_tokens is not None
        and candidate.uncached_input_tokens is not None
        and _regressed(
            base.uncached_input_tokens,
            candidate.uncached_input_tokens,
            policy.economics_tolerance,
        )
        and candidate.mean_score <= base.mean_score + policy.quality_tolerance
    ):
        return (
            RegressionVerdict.FAIL,
            "Candidate preserved quality but increased uncached input consumption.",
            (),
        )
    if base.provider_input_tokens is None or candidate.provider_input_tokens is None:
        warnings.append(
            "provider input usage was unavailable; context footprint is not "
            "a cost proxy"
        )
        if policy.require_provider_usage:
            return (
                RegressionVerdict.INCONCLUSIVE,
                (
                    "Quality was measured, but required end-to-end provider "
                    "usage was missing."
                ),
                tuple(warnings),
            )
        return (
            RegressionVerdict.WARN,
            (
                "Candidate preserved measured task quality; end-to-end token "
                "economics were not reported by the adapter."
            ),
            tuple(warnings),
        )
    if candidate.mean_score > base.mean_score + policy.quality_tolerance:
        return (
            RegressionVerdict.PASS,
            (
                "Candidate improved measured task quality; resource categories "
                "are reported separately."
            ),
            tuple(warnings),
        )
    if candidate.provider_input_tokens < base.provider_input_tokens:
        return (
            RegressionVerdict.PASS,
            (
                "Candidate preserved measured task quality while reducing "
                "end-to-end provider input."
            ),
            tuple(warnings),
        )
    return (
        RegressionVerdict.PASS,
        (
            "Candidate preserved measured task quality without an economics "
            "regression beyond tolerance."
        ),
        tuple(warnings),
    )


def _aggregate_delta(
    base: AggregateMetrics, candidate: AggregateMetrics
) -> dict[str, Any]:
    fields = (
        "success_rate",
        "mean_score",
        "initial_context_tokens",
        "provider_input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "estimated_cost_usd",
        "latency_seconds",
        "model_latency_seconds",
        "tool_latency_seconds",
        "turns",
        "tool_calls",
        "files_read",
        "searches",
        "commands",
        "exploration_breadth",
        "retries",
    )
    return {
        field: {
            "absolute": _subtract(getattr(base, field), getattr(candidate, field)),
            "fraction": _fraction(getattr(base, field), getattr(candidate, field)),
        }
        for field in fields
    }


def _tasks_from_config(
    value: Mapping[str, Any], root: Path
) -> tuple[VerificationTask, ...]:
    raw_tasks = value.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("verification config requires a nonempty `tasks` list")
    tasks: list[VerificationTask] = []
    for index, raw in enumerate(raw_tasks):
        task = _mapping(raw, f"tasks[{index}]")
        workspace = Path(str(task.get("workspace", ".")))
        resolved_workspace = (
            workspace if workspace.is_absolute() else (root / workspace).resolve()
        )
        raw_checks = task.get("checks", ())
        checks = tuple(
            tuple(str(part) for part in _sequence(command))
            for command in _sequence(raw_checks)
        )
        tasks.append(
            VerificationTask(
                task_id=_required_string(task, "id"),
                instruction=_required_string(task, "instruction"),
                workspace=resolved_workspace,
                checks=checks,
                expected_output=(
                    str(task["expected_output"])
                    if task.get("expected_output") is not None
                    else None
                ),
                allowed_files=tuple(
                    str(item) for item in _sequence(task.get("allowed_files", ()))
                ),
                timeout_seconds=float(task.get("timeout_seconds", 300)),
            )
        )
    return tuple(tasks)


def _agent_factory(value: Mapping[str, Any]) -> AgentFactory:
    adapter_type = str(value.get("type", "subprocess"))
    command = tuple(str(item) for item in _sequence(value.get("command", ())))
    if adapter_type == "subprocess":
        if not command:
            raise ValueError("subprocess agent requires `command`")
        adapter_id = str(value.get("adapter_id", "subprocess-context-regression-v1"))
        secret_environment = tuple(
            str(item) for item in _sequence(value.get("secret_environment", ()))
        )
        return lambda: SubprocessAgentAdapter(
            command,
            adapter_id=adapter_id,
            secret_environment=secret_environment,
        )
    if adapter_type == "codex":
        kwargs: dict[str, Any] = {}
        if command:
            kwargs["command"] = command
        kwargs["default_reasoning_effort"] = str(value.get("reasoning_effort", "low"))
        kwargs["default_sandbox"] = str(value.get("sandbox", "workspace-write"))
        return lambda: CodexCliAgentAdapter(**kwargs)
    raise ValueError(f"unsupported agent type: {adapter_type!r}")


def _verifier(task: VerificationTask) -> Any:
    if not task.checks:
        return None
    if len(task.checks) == 1:
        return CommandWorkspaceVerifier(
            task.checks[0],
            timeout_seconds=task.timeout_seconds,
        )
    return MultiCommandWorkspaceVerifier(
        task.checks,
        timeout_seconds=task.timeout_seconds,
    )


def _median(values: Sequence[float | int | None] | Any) -> float | None:
    present = [float(value) for value in values if value is not None]
    return statistics.median(present) if present else None


def _regressed(base: float, candidate: float, tolerance: float) -> bool:
    allowed = tolerance if base == 0 else abs(base) * tolerance
    return candidate > base + allowed


def _subtract(base: float | None, candidate: float | None) -> float | None:
    return candidate - base if base is not None and candidate is not None else None


def _fraction(base: float | None, candidate: float | None) -> float | None:
    if base in {None, 0} or candidate is None:
        return None
    return (candidate - base) / base


def _percent_delta(base: float | None, candidate: float | None) -> str:
    value = _fraction(base, candidate)
    return "—" if value is None else f"{value:+.1%}"


def _rate_delta(base: float, candidate: float) -> str:
    return f"{candidate - base:+.1%}"


def _delta(base: float, candidate: float) -> str:
    return f"{candidate - base:+.3f}"


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _tokens(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}s"


def _money(value: float | None) -> str:
    return "—" if value is None else f"${value:,.4f}"


def _count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, list | tuple | set | frozenset):
        return len(value)
    return None


def _milliseconds_to_seconds(value: Any) -> float | None:
    numeric = _optional_float(value)
    return numeric / 1_000 if numeric is not None else None


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return {} if value is None else _mapping(value, "parameters")


def _sequence(value: Any) -> Sequence[Any]:
    if not isinstance(value, list | tuple):
        raise ValueError("expected a JSON array")
    return value


def _required_string(value: Mapping[str, Any], key: str) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not selected.strip():
        raise ValueError(f"{key!r} must be a nonempty string")
    return selected


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected an integer")
    return value


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("expected a number")
    return float(value)


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return (
        value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    )
