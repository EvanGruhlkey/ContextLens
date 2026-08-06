"""Run genuine end-to-end ContextLens evaluations against fresh Codex workers."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from contextlens.analysis import (
    Measurement,
    PairedAnalyzer,
    PairedEffect,
    SavingsAnalyzer,
    Workload,
)
from contextlens.evaluation_records import (
    EvaluationInvocationRecord,
    InvocationRole,
    append_evaluation_record,
    read_evaluation_records,
)
from contextlens.evaluators import CodingTaskEvaluator
from contextlens.experiments import (
    AdaptiveAblationPlanner,
    AgentSettings,
    CodexCliAgentAdapter,
    ContextVariant,
    DeterministicExperimentCoordinator,
    DirectorySnapshot,
    PairedAdaptiveSearchRunner,
    ReplayCoordinator,
    ReplayRequest,
    ReplayResult,
    ReplayStatus,
    ReplayTask,
    ReplayWorker,
    ResourceLimits,
    SearchConfig,
    render_codex_prompt,
)
from contextlens.experiments.codex_cli import DEFAULT_CODEX_COMMAND
from contextlens.optimization import (
    ContextOptimizer,
    OptimizationObjective,
    OptimizationPolicy,
    VerifiedConfiguration,
)
from contextlens.policy import (
    mutations_from_policy,
    policy_from_verified_configuration,
)
from contextlens.profiler import ContextProfiler
from contextlens.reports import ReportBuilder, render_html, render_json, render_terminal
from contextlens.trace import ContextSource, TraceReader, record_replay_trace
from evals.cases import EvalCase, EvalSuite, VerificationSpec, get_suite
from evals.graders import HiddenCaseVerifier

FINAL_POLICIES = (
    "full_context",
    "contextlens",
    "matched_random",
)
SCHEMA_VERSION = "1.0"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_PROVIDER = "openai-chatgpt-codex-cli"
DEFAULT_REASONING = "low"


class _WindowsSleepGuard:
    """Keep long real-model runs awake without changing the user's power plan."""

    _CONTINUOUS = 0x80000000
    _SYSTEM_REQUIRED = 0x00000001

    def __init__(self) -> None:
        self.enabled = False

    def enable(self) -> None:
        if os.name != "nt":
            return
        result = ctypes.windll.kernel32.SetThreadExecutionState(
            self._CONTINUOUS | self._SYSTEM_REQUIRED
        )
        if result == 0:
            raise OSError("could not prevent system sleep during evaluation")
        self.enabled = True

    def disable(self) -> None:
        if not self.enabled:
            return
        ctypes.windll.kernel32.SetThreadExecutionState(
            self._CONTINUOUS
        )
        self.enabled = False


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Serializable case result plus aggregate-ready measurements."""

    case_id: str
    status: str
    directory: Path
    measurements: tuple[Measurement, ...]
    invocation_count: int
    error: str | None = None


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _source_tokens(source: ContextSource) -> int:
    if source.token_count is not None:
        return source.token_count
    return (len((source.content or "").encode("utf-8")) + 3) // 4


def _manifest_value(case: EvalCase) -> dict[str, Any]:
    verification = _verification_value(case.verification)
    return {
        "case_id": case.case_id,
        "suite": case.suite.value,
        "category": case.category.value,
        "instruction": case.instruction,
        "workspace_hashes": {
            name: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for name, content in sorted(case.workspace_files.items())
        },
        "allowed_files": list(case.allowed_files),
        "context": [
            {
                "source_id": source.source_id,
                "name": source.name,
                "kind": source.kind.value,
                "content_hash": source.content_hash,
                "token_count": source.token_count,
                "tags": list(source.tags),
            }
            for source in case.context
        ],
        "oracle_source_ids": list(case.oracle_source_ids),
        "hidden_verification_hash": hashlib.sha256(
            json.dumps(verification, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def _verification_value(spec: VerificationSpec) -> dict[str, Any]:
    return {
        "required_files": list(spec.required_files),
        "forbidden_files": list(spec.forbidden_files),
        "exact_files": dict(spec.exact_files),
        "contains": {key: list(value) for key, value in spec.contains.items()},
        "json_expectations": [
            {
                "path": item.path,
                "key_path": list(item.key_path),
                "expected": item.expected,
            }
            for item in spec.json_expectations
        ],
        "commands": [
            {
                "command": list(item.command),
                "expected_exit_code": item.expected_exit_code,
            }
            for item in spec.commands
        ],
        "patch_sha256": (
            hashlib.sha256(spec.patch.encode("utf-8")).hexdigest()
            if spec.patch
            else None
        ),
    }


def suite_manifest(cases: tuple[EvalCase, ...]) -> tuple[list[dict[str, Any]], str]:
    """Return a frozen public manifest and hash that also commits hidden checks."""

    values = [_manifest_value(case) for case in cases]
    digest = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return values, digest


def _provider_status(command: tuple[str, ...]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for name, suffix in (
        ("version", ("--version",)),
        ("login", ("login", "status")),
    ):
        completed = subprocess.run(
            (*command, *suffix),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        checks[name] = {
            "command": [*command, *suffix],
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        if completed.returncode != 0:
            raise RuntimeError(
                f"Codex provider {name} check failed: "
                f"{completed.stderr or completed.stdout}"
            )
    return checks


def _settings(model: str, reasoning: str) -> AgentSettings:
    return AgentSettings(
        provider=DEFAULT_PROVIDER,
        model=model,
        tools=("shell",),
        parameters={
            "reasoning_level": reasoning,
            "sandbox": "workspace-write",
            "ephemeral": True,
            "user_config_loaded": False,
            "ignore_rules": True,
        },
    )


def _worker(
    case: EvalCase,
    source_workspace: Path,
    settings: AgentSettings,
    adapter: CodexCliAgentAdapter,
    timeout_seconds: float,
) -> ReplayWorker:
    return ReplayWorker(
        adapter=adapter,
        snapshot=DirectorySnapshot(source_workspace),
        task=case.replay_task(),
        context=case.context,
        settings=settings,
        timeout_seconds=timeout_seconds,
        verifier=HiddenCaseVerifier(case, timeout_seconds=180.0),
    )


def _coordinator(worker: ReplayWorker, *, max_workers: int = 1) -> ReplayCoordinator:
    return ReplayCoordinator(
        worker,
        ResourceLimits(
            max_workers=max_workers,
            max_runs=max(100, max_workers),
            timeout_seconds=worker.timeout_seconds,
            retries=0,
        ),
        cache=None,
    )


def _evidence(result: ReplayResult) -> dict[str, Any]:
    value = dict(result.metadata)
    if result.outcome is not None:
        value.update(dict(result.outcome.metadata))
    return value


def _selected_context(
    context: tuple[ContextSource, ...], result: ReplayResult
) -> tuple[ContextSource, ...]:
    selected = set(result.context_source_ids)
    return tuple(source for source in context if source.source_id in selected)


def _fallback_prompt(
    result: ReplayResult,
    *,
    task: ReplayTask,
    context: tuple[ContextSource, ...],
    settings: AgentSettings,
) -> str:
    evidence = _evidence(result)
    prompt = evidence.get("rendered_prompt")
    if isinstance(prompt, str) and prompt:
        return prompt
    request = ReplayRequest(
        run_id=result.run_id,
        task=task,
        variant=ContextVariant(
            variant_id=result.variant_id,
            removed_source_ids=frozenset(result.removed_source_ids),
        ),
        context=_selected_context(context, result),
        settings=settings,
        workspace=result.workspace_path or "deleted-isolated-workspace",
        timeout_seconds=max(result.duration_seconds, 1.0),
    )
    return render_codex_prompt(request)


def _record(
    records_path: Path,
    result: ReplayResult,
    *,
    evaluation_run_id: str,
    case: EvalCase,
    settings: AgentSettings,
    evaluator: CodingTaskEvaluator,
    trial: int,
    policy: str,
    role: InvocationRole,
    intervention_id: str | None = None,
    parent_run_id: str | None = None,
) -> EvaluationInvocationRecord:
    evaluation = evaluator.evaluate(case.replay_task(), result)
    evidence = _evidence(result)
    verification = evidence.get("verification")
    grader_text = json.dumps(verification, sort_keys=True) if verification else None
    record = EvaluationInvocationRecord.from_replay_result(
        result,
        task=case.replay_task(),
        context=case.context,
        settings=settings,
        evaluation_run_id=evaluation_run_id,
        case_id=case.case_id,
        trial=trial,
        policy=policy,
        role=role,
        intervention_id=intervention_id,
        parent_run_id=parent_run_id,
        workspace_id=result.workspace_id or f"missing-{result.run_id}",
        started_at=result.started_at,
        ended_at=result.ended_at,
        rendered_prompt=_fallback_prompt(
            result,
            task=case.replay_task(),
            context=case.context,
            settings=settings,
        ),
        reasoning_level=str(settings.parameters["reasoning_level"]),
        grader_input=grader_text,
        raw_grader_response=grader_text,
        parsed_score={
            "scores": dict(evaluation.scores),
            "dimensions": dict(evaluation.dimensions),
            "success": evaluation.success,
            "utility_score": evaluation.utility_score,
        },
        metadata={
            "provider_evidence": evidence,
            "evaluator_id": evaluator.evaluator_id,
            "workspace_deleted_after_replay": True,
            "response_source": "fresh_codex_exec_jsonl",
        },
    )
    append_evaluation_record(records_path, record)
    return record


def _trial_number(value: str) -> int:
    marker = value.rsplit("trial-", 1)[-1]
    return max(1, int(marker))


def _candidate_plan_value(plan: Any) -> dict[str, Any]:
    return {
        "objective": plan.objective,
        "budget": plan.budget,
        "planned_runs": plan.planned_runs,
        "remaining_budget": plan.remaining_budget,
        "experiments": [
            {
                "experiment_id": experiment.experiment_id,
                "candidate": {
                    "source_id": experiment.candidate.source_id,
                    "mutation": experiment.candidate.mutation.to_dict(),
                    "expected_token_savings": (
                        experiment.candidate.expected_token_savings
                    ),
                    "uncertainty": experiment.candidate.uncertainty,
                    "probability_of_meaningful_effect": (
                        experiment.candidate.probability_of_meaningful_effect
                    ),
                    "evaluator_reliability": (
                        experiment.candidate.evaluator_reliability
                    ),
                    "estimated_replay_cost": (
                        experiment.candidate.estimated_replay_cost
                    ),
                    "priority": experiment.candidate.priority,
                    "reasons": list(experiment.candidate.reasons),
                },
                "runs": [
                    {
                        "job_id": run.job_id,
                        "pair_id": run.pair_id,
                        "variant": run.variant,
                        "mutation": run.mutation.to_dict() if run.mutation else None,
                    }
                    for run in experiment.runs
                ],
            }
            for experiment in plan.experiments
        ],
    }


def _effect_value(effect: PairedEffect) -> dict[str, Any]:
    value = asdict(effect)
    value["verdict"] = effect.verdict.value
    value["evidence_scope"] = effect.evidence_scope.value
    return value


def _control_variants(
    case: EvalCase,
    verified: VerifiedConfiguration,
) -> tuple[ContextVariant, ...]:
    policy = policy_from_verified_configuration(case.context, verified)
    contextlens = mutations_from_policy(case.context, policy)
    target_tokens = verified.candidate.removed_tokens if verified.accepted else 0
    random_ids = _random_until(case, target_tokens)
    return (
        ContextVariant("full_context", description="unmodified complete context"),
        ContextVariant(
            "contextlens",
            description="optimizer-selected and freshly verified ContextLens policy",
            mutations=contextlens,
        ),
        ContextVariant(
            "matched_random",
            removed_source_ids=frozenset(random_ids),
            description="seeded random removal matched to policy tokens",
        ),
    )


def _largest_until(
    context: tuple[ContextSource, ...], target_tokens: int
) -> tuple[str, ...]:
    if target_tokens <= 0:
        return ()
    ordered = sorted(
        context,
        key=lambda source: (-_source_tokens(source), source.source_id),
    )
    selected: list[str] = []
    total = 0
    for source in ordered:
        if total >= target_tokens:
            break
        selected.append(source.source_id)
        total += _source_tokens(source)
    return tuple(selected)


def _random_until(case: EvalCase, target_tokens: int) -> tuple[str, ...]:
    if target_tokens <= 0:
        return ()
    seed = int(hashlib.sha256(case.case_id.encode("utf-8")).hexdigest()[:16], 16)
    generator = random.Random(seed)
    values = list(case.context)
    generator.shuffle(values)
    selected: list[str] = []
    total = 0
    for source in values:
        if total >= target_tokens:
            break
        selected.append(source.source_id)
        total += _source_tokens(source)
    return tuple(selected)


def _run_case(
    case: EvalCase,
    *,
    run_dir: Path,
    evaluation_run_id: str,
    trials: int,
    model: str,
    reasoning: str,
    timeout_seconds: float,
    max_experiments: int,
) -> CaseOutcome:
    case_dir = run_dir / "cases" / case.case_id
    case_dir.mkdir(parents=True, exist_ok=False)
    records_path = case_dir / "invocations.jsonl"
    workspace = case.materialize_workspace(case_dir / "workspace-source")
    settings = _settings(model, reasoning)
    adapter = CodexCliAgentAdapter(
        environment={"PYTHONDONTWRITEBYTECODE": "1"}
    )
    evaluator = CodingTaskEvaluator(objective="quality")
    worker = _worker(case, workspace, settings, adapter, timeout_seconds)
    coordinator = _coordinator(worker)
    all_results: list[ReplayResult] = []

    profiler_result = coordinator.run((ContextVariant("profiler_baseline"),))[0]
    all_results.append(profiler_result)
    _record(
        records_path,
        profiler_result,
        evaluation_run_id=evaluation_run_id,
        case=case,
        settings=settings,
        evaluator=evaluator,
        trial=1,
        policy="profiler_baseline",
        role=InvocationRole.BASELINE_WORKER,
    )
    if profiler_result.status is not ReplayStatus.COMPLETED:
        failure = profiler_result.error or profiler_result.status
        raise RuntimeError(
            f"profiler baseline failed: {failure}"
        )

    trace_path = case_dir / "traces" / "profiler-baseline.jsonl"
    recorded = record_replay_trace(
        trace_path,
        task=case.replay_task(),
        context=case.context,
        settings=settings,
        result=profiler_result,
    )
    events = tuple(TraceReader(trace_path).events())
    profile = ContextProfiler().profile(events, recorded.observation)
    _json_dump(case_dir / "profile.json", profile.to_dict())

    paired_runs = max(2, trials)
    candidate_coordinator = DeterministicExperimentCoordinator(
        paired_runs=paired_runs
    )
    candidate_plan = candidate_coordinator.plan(
        case.context,
        profile.profiles,
        experiment_budget=2 * paired_runs * max(1, max_experiments - 1),
        objective="balanced",
    )
    _json_dump(case_dir / "candidate-plan.json", _candidate_plan_value(candidate_plan))

    planner = AdaptiveAblationPlanner(
        case.context,
        profiles=profile.profiles,
        config=SearchConfig(
            quality_tolerance=0.02,
            max_experiments=max_experiments,
            batch_size=1,
        ),
    )

    def record_paired(invocation: Any) -> None:
        role = (
            InvocationRole.BASELINE_WORKER
            if invocation.variant_id == planner.BASELINE_ID
            else InvocationRole.REPLAY_WORKER
        )
        _record(
            records_path,
            invocation.result,
            evaluation_run_id=evaluation_run_id,
            case=case,
            settings=settings,
            evaluator=evaluator,
            trial=_trial_number(invocation.trial_id),
            policy=(
                "adaptive_baseline"
                if invocation.variant_id == planner.BASELINE_ID
                else "adaptive_ablation"
            ),
            role=role,
            intervention_id=invocation.trial_id.rsplit(":trial-", 1)[0],
            parent_run_id=profiler_result.run_id,
        )

    paired = PairedAdaptiveSearchRunner(
        planner,
        _coordinator(worker),
        evaluator,
        score_name="quality",
        trials=trials,
        analyzer=PairedAnalyzer(equivalence_tolerance=0.02),
        on_invocation=record_paired,
    ).run()
    all_results.extend(paired.replay_results)
    _json_dump(
        case_dir / "adaptive-search.json",
        {
            "report": {
                "baseline": (
                    asdict(paired.report.baseline) if paired.report.baseline else None
                ),
                "experiments_planned": paired.report.experiments_planned,
                "planned_context_tokens": paired.report.planned_context_tokens,
                "estimated_cost_usd": paired.report.estimated_cost_usd,
                "stopping_reason": paired.report.stopping_reason,
                "recommended_removals": list(paired.report.recommended_removals),
                "nodes": [
                    {
                        "group": asdict(node.group),
                        "decision": node.decision.value,
                        "variant_id": node.variant_id,
                        "observation": (
                            asdict(node.observation) if node.observation else None
                        ),
                        "quality_delta": node.quality_delta,
                        "reason": node.reason,
                        "children": list(node.children),
                    }
                    for node in paired.report.nodes
                ],
            },
            "effects": [_effect_value(effect) for effect in paired.effects],
            "errors": [asdict(error) for error in paired.errors],
        },
    )
    initial_scores = [
        measurement.score
        for measurement in paired.measurements
        if measurement.variant_id == planner.BASELINE_ID
        and measurement.trial_id.startswith("initial-baseline:")
    ]
    if not initial_scores:
        raise RuntimeError("adaptive search produced no successful baseline score")
    optimizer = ContextOptimizer(case.context, profiles=profile.profiles)
    full_tokens = sum(_source_tokens(source) for source in case.context)
    optimization_policy = OptimizationPolicy(
        objective=OptimizationObjective.TOKEN_BUDGET,
        token_budget=full_tokens,
        quality_tolerance=0.02,
    )
    candidate = optimizer.propose(paired.report, optimization_policy)
    verified = optimizer.verify(
        candidate,
        coordinator=_coordinator(worker),
        evaluator=evaluator,
        score_name="quality",
        baseline_score=statistics.fmean(initial_scores),
        policy=optimization_policy,
    )
    all_results.append(verified.replay_result)
    _record(
        records_path,
        verified.replay_result,
        evaluation_run_id=evaluation_run_id,
        case=case,
        settings=settings,
        evaluator=evaluator,
        trial=1,
        policy="optimizer_verification",
        role=InvocationRole.VERIFICATION_WORKER,
        intervention_id=verified.candidate.candidate_id,
        parent_run_id=profiler_result.run_id,
    )
    context_policy = policy_from_verified_configuration(case.context, verified)
    (case_dir / "context-policy.json").write_text(
        context_policy.to_json(), encoding="utf-8"
    )
    (case_dir / "context-policy.yaml").write_text(
        context_policy.to_yaml(), encoding="utf-8"
    )

    controls = _control_variants(case, verified)
    final_measurements: list[Measurement] = []
    for trial in range(1, trials + 1):
        results = _coordinator(worker, max_workers=min(2, len(controls))).run(controls)
        for result in results:
            all_results.append(result)
            _record(
                records_path,
                result,
                evaluation_run_id=evaluation_run_id,
                case=case,
                settings=settings,
                evaluator=evaluator,
                trial=trial,
                policy=result.variant_id,
                role=InvocationRole.CONTROL_WORKER,
                intervention_id="final-policy-comparison",
                parent_run_id=verified.replay_result.run_id,
            )
            evaluation = evaluator.evaluate(case.replay_task(), result)
            final_measurements.append(
                Measurement.from_result(
                    result,
                    evaluation,
                    trial_id=f"final:trial-{trial}",
                    score_name="quality",
                )
            )

    report_builder = (
        ReportBuilder(f"ContextLens real LLM eval: {case.case_id}")
        .add_profile(profile)
        .add_search(paired.report)
        .add_verified_configuration(verified)
        .add_runs(tuple(all_results))
        .metadata(
            evaluation_run_id=evaluation_run_id,
            case_id=case.case_id,
            suite=case.suite.value,
            provider=settings.provider,
            model=settings.model,
            trials=trials,
            fresh_processes=True,
            cache_enabled=False,
        )
    )
    for effect in paired.effects:
        report_builder.add_effect(effect)
    if len(final_measurements) == trials * len(FINAL_POLICIES):
        final_effect = PairedAnalyzer(equivalence_tolerance=0.02).analyze(
            tuple(final_measurements),
            baseline_variant_id="full_context",
            ablated_variant_id="contextlens",
        )
        report_builder.add_effect(
            final_effect,
            source_id="contextlens-final-policy",
            name="ContextLens final policy",
        )
        report_builder.add_savings(
            SavingsAnalyzer().recommend(
                final_effect,
                Workload(runs_per_day=1000, projection_days=30),
                source_id="contextlens-final-policy",
                name="ContextLens final policy",
            )
        )
    report = report_builder.build()
    (case_dir / "report.json").write_text(render_json(report), encoding="utf-8")
    (case_dir / "report.html").write_text(render_html(report), encoding="utf-8")
    (case_dir / "report.txt").write_text(render_terminal(report), encoding="utf-8")
    _json_dump(
        case_dir / "result.json",
        {
            "case_id": case.case_id,
            "status": "complete",
            "profiler_trace": str(trace_path.relative_to(run_dir)),
            "candidate_accepted": verified.accepted,
            "candidate_removed_source_ids": list(candidate.removed_source_ids),
            "final_policy_count": len(FINAL_POLICIES),
            "final_trial_count": trials,
            "invocation_count": len(read_evaluation_records(records_path)),
        },
    )
    return CaseOutcome(
        case_id=case.case_id,
        status="complete",
        directory=case_dir,
        measurements=tuple(final_measurements),
        invocation_count=len(read_evaluation_records(records_path)),
    )


def _measurement_from_record(record: EvaluationInvocationRecord) -> Measurement:
    """Rebuild an aggregate measurement from one durable control record."""

    parsed = record.parsed_score
    if not isinstance(parsed, dict):
        raise ValueError(f"record {record.record_id} has no parsed score")
    scores = parsed.get("scores")
    if not isinstance(scores, dict) or "quality" not in scores:
        raise ValueError(f"record {record.record_id} has no quality score")
    return Measurement(
        task_id=record.case_id,
        trial_id=f"final:trial-{record.trial}",
        variant_id=record.policy,
        score=float(scores["quality"]),
        success=bool(scores.get("success", parsed.get("success", False))),
        input_tokens=record.provider_input_tokens or 0,
        output_tokens=record.provider_output_tokens or 0,
        cost_usd=record.estimated_cost_usd or 0.0,
        latency_seconds=record.latency_seconds,
        tool_calls=len(record.tool_calls),
        retries=record.retry_count,
    )


def _completed_case_outcome(
    case: EvalCase,
    *,
    run_dir: Path,
    evaluation_run_id: str,
    trials: int,
) -> CaseOutcome | None:
    """Return a score-blind, structurally complete outcome for crash recovery."""

    case_dir = run_dir / "cases" / case.case_id
    result_path = case_dir / "result.json"
    records_path = case_dir / "invocations.jsonl"
    if not result_path.is_file() or not records_path.is_file():
        return None
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        records = read_evaluation_records(records_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict) or result.get("status") != "complete":
        return None
    if result.get("invocation_count") != len(records):
        return None
    required_artifacts = (
        "profile.json",
        "candidate-plan.json",
        "adaptive-search.json",
        "context-policy.json",
        "context-policy.yaml",
        "report.json",
        "report.html",
        "report.txt",
    )
    if any(not (case_dir / name).is_file() for name in required_artifacts):
        return None
    if len(tuple((case_dir / "traces").glob("*.jsonl"))) != 1:
        return None
    if any(
        record.evaluation_run_id != evaluation_run_id
        or record.case_id != case.case_id
        for record in records
    ):
        return None
    controls = tuple(record for record in records if record.policy in FINAL_POLICIES)
    policy_counts = Counter(record.policy for record in records)
    if (
        policy_counts["profiler_baseline"] != 1
        or policy_counts["optimizer_verification"] != 1
        or policy_counts["adaptive_baseline"] < 2 * trials
        or policy_counts["adaptive_ablation"] < trials
    ):
        return None
    expected = {
        (policy, trial)
        for policy in FINAL_POLICIES
        for trial in range(1, trials + 1)
    }
    observed = {(record.policy, record.trial) for record in controls}
    if observed != expected or len(controls) != len(expected):
        return None
    try:
        measurements = tuple(_measurement_from_record(record) for record in controls)
    except (TypeError, ValueError):
        return None
    return CaseOutcome(
        case_id=case.case_id,
        status="complete",
        directory=case_dir,
        measurements=measurements,
        invocation_count=len(records),
    )


def _resume_run(
    args: argparse.Namespace,
    cases: tuple[EvalCase, ...],
    manifest_values: list[dict[str, Any]],
    manifest_hash: str,
) -> tuple[Path, str, dict[str, Any], list[CaseOutcome], tuple[EvalCase, ...]]:
    """Prepare a score-blind, case-atomic continuation of an interrupted run."""

    run_dir = args.resume.resolve()
    manifest_path = run_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        frozen_cases = json.loads(
            (run_dir / "case-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot resume invalid run: {error}") from error
    if not isinstance(manifest, dict):
        raise SystemExit("cannot resume: manifest is not an object")
    required = {
        "suite": args.suite,
        "trials": args.trials,
        "model": args.model,
        "reasoning_level": args.reasoning,
        "case_manifest_hash": manifest_hash,
        "max_experiments_per_case": args.max_experiments,
    }
    drift = {
        key: (manifest.get(key), value)
        for key, value in required.items()
        if manifest.get(key) != value
    }
    if drift or frozen_cases != manifest_values:
        raise SystemExit(
            "cannot resume with changed settings or case corpus: "
            f"{drift or 'case manifest drift'}"
        )
    evaluation_run_id = str(manifest.get("evaluation_run_id", ""))
    if not evaluation_run_id or run_dir.name != evaluation_run_id:
        raise SystemExit("cannot resume: evaluation run identity is inconsistent")

    retained: list[CaseOutcome] = []
    pending: list[EvalCase] = []
    for case in cases:
        outcome = _completed_case_outcome(
            case,
            run_dir=run_dir,
            evaluation_run_id=evaluation_run_id,
            trials=args.trials,
        )
        if outcome is None:
            pending.append(case)
        else:
            retained.append(outcome)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_root = run_dir / "recovery-attempts" / stamp
    archived: list[str] = []
    for case in pending:
        case_dir = run_dir / "cases" / case.case_id
        if not case_dir.exists():
            continue
        archive_root.mkdir(parents=True, exist_ok=True)
        destination = archive_root / case.case_id
        if destination.exists():
            destination = archive_root / f"{case.case_id}-{uuid4().hex[:8]}"
        case_dir.rename(destination)
        archived.append(str(destination.relative_to(run_dir)))

    history = manifest.get("resume_history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "resumed_at": _timestamp(),
            "policy": (
                "retain every structurally complete case regardless of score; "
                "archive and restart every incomplete case from its profiler call"
            ),
            "retained_case_ids": sorted(item.case_id for item in retained),
            "restarted_case_ids": sorted(case.case_id for case in pending),
            "archived_directories": archived,
        }
    )
    manifest.update(
        {
            "status": "running",
            "ended_at": None,
            "resume_history": history,
            "crash_recovery_is_case_atomic": True,
            "crash_recovery_score_blind": True,
        }
    )
    _json_dump(manifest_path, manifest)
    return run_dir, evaluation_run_id, manifest, retained, tuple(pending)


def _aggregate(
    run_dir: Path,
    outcomes: tuple[CaseOutcome, ...],
    *,
    suite: str,
    trials: int,
) -> dict[str, Any]:
    records = read_evaluation_records(run_dir / "invocations.jsonl")
    discovery_records = tuple(
        record for record in records if record.policy not in FINAL_POLICIES
    )
    replay_records = tuple(
        record
        for record in discovery_records
        if record.policy.startswith("adaptive_")
        or record.policy == "optimizer_verification"
    )
    measurements = tuple(
        measurement for outcome in outcomes for measurement in outcome.measurements
    )
    effects: dict[str, dict[str, Any]] = {}
    analyzer = PairedAnalyzer(equivalence_tolerance=0.02)
    for policy in FINAL_POLICIES[1:]:
        try:
            effect = analyzer.analyze(
                measurements,
                baseline_variant_id="full_context",
                ablated_variant_id=policy,
            )
        except ValueError as error:
            effects[policy] = {"error": str(error)}
        else:
            effects[policy] = _effect_value(effect)
    comparisons: dict[str, dict[str, int]] = {}
    full = {
        (item.task_id, item.trial_id): item
        for item in measurements
        if item.variant_id == "full_context"
    }
    for policy in FINAL_POLICIES[1:]:
        counts = {"wins": 0, "ties": 0, "losses": 0}
        for item in measurements:
            if item.variant_id != policy:
                continue
            baseline = full.get((item.task_id, item.trial_id))
            if baseline is None:
                continue
            delta = item.score - baseline.score
            if delta > 0.02:
                counts["wins"] += 1
            elif delta < -0.02:
                counts["losses"] += 1
            else:
                counts["ties"] += 1
        comparisons[policy] = counts
    policy_summaries: dict[str, dict[str, Any]] = {}
    for policy in FINAL_POLICIES:
        values = [item for item in measurements if item.variant_id == policy]
        policy_records = [record for record in records if record.policy == policy]
        cached = [
            record.provider_cached_tokens
            for record in policy_records
            if record.provider_cached_tokens is not None
        ]
        uncached = [
            record.provider_input_tokens - record.provider_cached_tokens
            for record in policy_records
            if record.provider_input_tokens is not None
            and record.provider_cached_tokens is not None
        ]
        policy_summaries[policy] = {
            "invocations": len(values),
            "successful_tasks": sum(item.success for item in values),
            "mean_quality": statistics.fmean(item.score for item in values)
            if values
            else None,
            "success_rate": statistics.fmean(item.success for item in values)
            if values
            else None,
            "mean_input_tokens": statistics.fmean(item.input_tokens for item in values)
            if values
            else None,
            "mean_output_tokens": statistics.fmean(
                item.output_tokens for item in values
            )
            if values
            else None,
            "mean_cached_input_tokens": statistics.fmean(cached)
            if cached
            else None,
            "mean_uncached_input_tokens": statistics.fmean(uncached)
            if uncached
            else None,
            "mean_latency_seconds": statistics.fmean(
                item.latency_seconds for item in values
            )
            if values
            else None,
            "mean_tool_calls": statistics.fmean(item.tool_calls for item in values)
            if values
            else None,
            "total_retries": sum(item.retries for item in values),
            "failed_invocations": sum(
                record.status != "completed" for record in policy_records
            ),
            "estimated_cost_usd": None,
        }
    full_summary = policy_summaries["full_context"]
    optimized_summary = policy_summaries["contextlens"]
    mean_input_saved = (
        float(full_summary["mean_input_tokens"])
        - float(optimized_summary["mean_input_tokens"])
    )
    mean_uncached_saved = (
        float(full_summary["mean_uncached_input_tokens"])
        - float(optimized_summary["mean_uncached_input_tokens"])
    )
    discovery_input_tokens = sum(
        record.provider_input_tokens or 0 for record in discovery_records
    )
    break_even_runs = (
        max(1, math.ceil(discovery_input_tokens / mean_input_saved))
        if mean_input_saved > 0
        else None
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "suite": suite,
        "trials": trials,
        "case_count": len(outcomes),
        "completed_case_count": sum(item.status == "complete" for item in outcomes),
        "measurement_count": len(measurements),
        "attempted_model_invocation_count": len(records),
        "contextlens_discovery": {
            "invocations": len(discovery_records),
            "replay_workers": len(replay_records),
            "input_tokens": sum(
                record.provider_input_tokens or 0 for record in discovery_records
            ),
            "cached_input_tokens": sum(
                record.provider_cached_tokens or 0 for record in discovery_records
            ),
            "uncached_input_tokens": sum(
                (record.provider_input_tokens or 0)
                - (record.provider_cached_tokens or 0)
                for record in discovery_records
            ),
            "output_tokens": sum(
                record.provider_output_tokens or 0 for record in discovery_records
            ),
            "latency_seconds": sum(
                record.latency_seconds for record in discovery_records
            ),
        },
        "policy_summaries": policy_summaries,
        "optimized_production_effect": {
            "mean_input_tokens_saved": mean_input_saved,
            "mean_uncached_input_tokens_saved": mean_uncached_saved,
            "mean_output_tokens_saved": (
                float(full_summary["mean_output_tokens"])
                - float(optimized_summary["mean_output_tokens"])
            ),
            "mean_latency_seconds_saved": (
                float(full_summary["mean_latency_seconds"])
                - float(optimized_summary["mean_latency_seconds"])
            ),
            "break_even_repeated_runs": break_even_runs,
        },
        "paired_effects_vs_full_context": effects,
        "win_tie_loss_vs_full_context": comparisons,
        "cost_note": (
            "ChatGPT-authenticated Codex does not expose per-invocation USD cost; "
            "token and latency savings are measured, USD savings and break-even "
            "remain unavailable."
        ),
    }
    _json_dump(run_dir / "aggregate.json", result)
    return result


def _merge_records(run_dir: Path, outcomes: tuple[CaseOutcome, ...]) -> int:
    destination = run_dir / "invocations.jsonl"
    count = 0
    seen: set[str] = set()
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        for outcome in sorted(outcomes, key=lambda item: item.case_id):
            source = outcome.directory / "invocations.jsonl"
            if not source.exists():
                continue
            for record in read_evaluation_records(source):
                if record.record_id in seen:
                    raise ValueError(f"duplicate merged record ID: {record.record_id}")
                seen.add(record.record_id)
                stream.write(
                    json.dumps(
                        record.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                stream.write("\n")
                count += 1
        stream.flush()
        os.fsync(stream.fileno())
    return count


def _write_checksums(run_dir: Path) -> None:
    values: dict[str, str] = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name == "checksums.json":
            continue
        values[path.relative_to(run_dir).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    _json_dump(run_dir / "checksums.json", values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", choices=[item.value for item in EvalSuite], required=True
    )
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reasoning", default=DEFAULT_REASONING)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--max-experiments", type=int, default=2)
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="run only the named case ID (repeatable)",
    )
    parser.add_argument("--output-root", type=Path, default=Path("evals/artifacts"))
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help=(
            "continue an interrupted run by retaining structurally complete cases "
            "and restarting every incomplete case"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.trials < 1:
        raise SystemExit("--trials must be positive")
    if args.max_experiments < 2:
        raise SystemExit("--max-experiments must be at least 2")
    cases = get_suite(args.suite)
    if args.case:
        requested = set(args.case)
        cases = tuple(case for case in cases if case.case_id in requested)
        missing = requested - {case.case_id for case in cases}
        if missing:
            raise SystemExit(f"unknown case IDs: {sorted(missing)}")
    if args.suite == EvalSuite.HELDOUT.value and (
        len(cases) < 20 or args.trials < 3
    ):
        raise SystemExit("heldout requires at least 20 cases and 3 trials")
    manifest_values, manifest_hash = suite_manifest(cases)
    command = DEFAULT_CODEX_COMMAND
    if args.resume is not None:
        run_dir, evaluation_run_id, manifest, outcomes, pending_cases = _resume_run(
            args,
            cases,
            manifest_values,
            manifest_hash,
        )
    else:
        evaluation_run_id = (
            f"{args.suite}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
            f"{uuid4().hex[:8]}"
        )
        run_dir = args.output_root.resolve() / evaluation_run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        _json_dump(run_dir / "case-manifest.json", manifest_values)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "evaluation_run_id": evaluation_run_id,
            "suite": args.suite,
            "trials": args.trials,
            "case_ids": [case.case_id for case in cases],
            "case_manifest_hash": manifest_hash,
            "provider": DEFAULT_PROVIDER,
            "model": args.model,
            "reasoning_level": args.reasoning,
            "codex_command": list(command),
            "started_at": _timestamp(),
            "ended_at": None,
            "status": "running",
            "fresh_process_per_invocation": True,
            "conversation_reuse": False,
            "response_cache_enabled": False,
            "user_config_loaded": False,
            "windows_sandbox_override": "elevated",
            "system_sleep_prevented_during_run": os.name == "nt",
            "retries": 0,
            "final_policies": list(FINAL_POLICIES),
            "max_experiments_per_case": args.max_experiments,
            "requested_case_workers": args.workers,
        }
        _json_dump(run_dir / "manifest.json", manifest)
        outcomes = []
        pending_cases = cases
    exit_code = 0
    sleep_guard = _WindowsSleepGuard()
    try:
        sleep_guard.enable()
        manifest["provider_preflight"] = _provider_status(command)
        _json_dump(run_dir / "manifest.json", manifest)
        default_workers = 1 if args.suite == EvalSuite.SMOKE.value else 3
        worker_count = args.workers or default_workers
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            pending = {
                executor.submit(
                    _run_case,
                    case,
                    run_dir=run_dir,
                    evaluation_run_id=evaluation_run_id,
                    trials=args.trials,
                    model=args.model,
                    reasoning=args.reasoning,
                    timeout_seconds=args.timeout_seconds,
                    max_experiments=args.max_experiments,
                ): case
                for case in pending_cases
            }
            for future in as_completed(pending):
                case = pending[future]
                try:
                    outcome = future.result()
                    detail = (
                        f"{case.case_id}: complete "
                        f"({outcome.invocation_count} invocations)"
                    )
                    print(
                        detail,
                        flush=True,
                    )
                except Exception as error:  # retain every other case and artifact
                    exit_code = 1
                    case_dir = run_dir / "cases" / case.case_id
                    outcome = CaseOutcome(
                        case_id=case.case_id,
                        status="failed",
                        directory=case_dir,
                        measurements=(),
                        invocation_count=(
                            len(
                                read_evaluation_records(
                                    case_dir / "invocations.jsonl"
                                )
                            )
                            if (case_dir / "invocations.jsonl").exists()
                            else 0
                        ),
                        error=f"{type(error).__name__}: {error}",
                    )
                    _json_dump(
                        case_dir / "result.json",
                        {
                            "case_id": case.case_id,
                            "status": "failed",
                            "error": outcome.error,
                            "invocation_count": outcome.invocation_count,
                        },
                    )
                    print(f"{case.case_id}: FAILED: {outcome.error}", flush=True)
                outcomes.append(outcome)
        ordered = tuple(sorted(outcomes, key=lambda item: item.case_id))
        invocation_count = _merge_records(run_dir, ordered)
        aggregate = _aggregate(
            run_dir,
            ordered,
            suite=args.suite,
            trials=args.trials,
        )
        manifest.update(
            {
                "status": "complete" if exit_code == 0 else "failed",
                "ended_at": _timestamp(),
                "attempted_invocation_count": invocation_count,
                "completed_case_count": aggregate["completed_case_count"],
                "case_results": [
                    {
                        "case_id": item.case_id,
                        "status": item.status,
                        "invocation_count": item.invocation_count,
                        "error": item.error,
                    }
                    for item in ordered
                ],
            }
        )
    except Exception as error:
        exit_code = 1
        manifest.update(
            {
                "status": "failed",
                "ended_at": _timestamp(),
                "fatal_error": f"{type(error).__name__}: {error}",
                "attempted_invocation_count": sum(
                    item.invocation_count for item in outcomes
                ),
            }
        )
        print(f"EVALUATION FAILED: {manifest['fatal_error']}", file=sys.stderr)
    finally:
        sleep_guard.disable()
    _json_dump(run_dir / "manifest.json", manifest)
    _write_checksums(run_dir)
    print(f"run_dir={run_dir}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
