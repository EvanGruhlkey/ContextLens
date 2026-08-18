"""Command-line workflows for ContextLens."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from contextlens import __version__
from contextlens.analysis import (
    EvidenceScope,
    Measurement,
    PairedAnalyzer,
    SavingsAnalyzer,
    Workload,
)
from contextlens.ci import (
    StaticCiPolicy,
    evaluate_static_ci,
    evaluate_verified_ci,
    write_summary,
)
from contextlens.evaluators import ExactMatchEvaluator, TestResultsEvaluator
from contextlens.experiments import (
    AdaptiveAblationPlanner,
    AdaptiveSearchRunner,
    AgentSettings,
    DirectorySnapshot,
    MemoryReplayCache,
    ReplayCoordinator,
    ReplayStatus,
    ReplayTask,
    ReplayWorker,
    ResourceLimits,
    SearchConfig,
    SubprocessAgentAdapter,
)
from contextlens.minimize import (
    minimize_repository,
    render_minimization_terminal,
)
from contextlens.optimization import (
    ContextOptimizer,
    ContextValuePredictor,
    OptimizationObjective,
    OptimizationPolicy,
)
from contextlens.policy import ContextPolicy, policy_from_report
from contextlens.profiler import ContextProfiler, RunObservation
from contextlens.regression import (
    render_verification_markdown,
    render_verification_terminal,
    verify_repository,
)
from contextlens.reports import (
    Report,
    ReportBuilder,
    render_csv,
    render_html,
    render_json,
    render_terminal,
)
from contextlens.repository import (
    diff_repository,
    render_diff_terminal,
    render_markdown,
    render_scan_terminal,
    scan_repository,
)
from contextlens.runtime import apply_context_policy
from contextlens.trace import ArtifactStore, ContextSource, TraceReader


def main(argv: list[str] | None = None) -> None:
    """Run the ContextLens CLI."""
    parser = _parser()
    arguments = parser.parse_args(argv)
    if not hasattr(arguments, "handler"):
        parser.print_help()
        return
    try:
        code = arguments.handler(arguments)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        parser.exit(2, f"contextlens: {error}\n")
    if code:
        raise SystemExit(code)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contextlens",
        description="Test repository agent context changes for regressions.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")

    scan = commands.add_parser(
        "scan",
        help="discover and statically inspect repository agent context",
    )
    scan.add_argument(
        "target",
        nargs="?",
        type=Path,
        default=Path("."),
        help="repository path (default: current directory)",
    )
    scan.add_argument("--request-id", help=argparse.SUPPRESS)
    scan.add_argument("--observation", type=Path, help=argparse.SUPPRESS)
    scan.add_argument("--artifacts", type=Path, help=argparse.SUPPRESS)
    scan.add_argument(
        "--format",
        choices=("terminal", "json", "markdown", "csv", "html"),
        default="terminal",
    )
    scan.add_argument("--output", type=Path)
    scan.set_defaults(handler=_scan)

    context_diff = commands.add_parser(
        "diff",
        help="compare worktree agent context with a Git base",
    )
    context_diff.add_argument("repository", nargs="?", type=Path, default=Path("."))
    context_diff.add_argument("--base")
    context_diff.add_argument(
        "--format", choices=("terminal", "json", "markdown"), default="terminal"
    )
    context_diff.add_argument("--output", type=Path)
    context_diff.set_defaults(handler=_diff)

    verify = commands.add_parser(
        "verify",
        help="run matched base-versus-candidate context trials",
    )
    verify.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=Path(".contextlens/evals.json"),
    )
    verify.add_argument("--repository", type=Path, default=Path("."))
    verify.add_argument("--base")
    verify.add_argument(
        "--format", choices=("terminal", "json", "markdown"), default="terminal"
    )
    verify.add_argument("--output", type=Path)
    verify.set_defaults(handler=_verify)

    minimize = commands.add_parser(
        "minimize",
        help="generate static candidates and verify a safe context patch",
    )
    minimize.add_argument("paths", nargs="*")
    minimize.add_argument("--repository", type=Path, default=Path("."))
    minimize.add_argument("--config", type=Path)
    minimize.add_argument("--max-candidates", type=int, default=8)
    minimize.add_argument("--patch-output", type=Path)
    minimize.add_argument("--report-output", type=Path)
    minimize.add_argument("--format", choices=("terminal", "json"), default="terminal")
    minimize.set_defaults(handler=_minimize)

    ci = commands.add_parser(
        "ci",
        help="run static or verified agent-context regression gates",
    )
    ci.add_argument("--mode", choices=("static", "verified"), default="static")
    ci.add_argument("--repository", type=Path, default=Path("."))
    ci.add_argument("--base")
    ci.add_argument("--config", type=Path, default=Path(".contextlens/evals.json"))
    ci.add_argument("--max-context-increase", type=float)
    ci.add_argument("--max-duplicate-increase", type=int)
    ci.add_argument("--max-stale-increase", type=int)
    ci.add_argument("--json-output", type=Path)
    ci.add_argument("--summary", type=Path)
    ci.set_defaults(handler=_ci)

    record = commands.add_parser(
        "record",
        help="run an instrumented agent that writes a ContextLens trace",
    )
    record.add_argument("--output", required=True, type=Path)
    record.add_argument("agent_command", nargs=argparse.REMAINDER)
    record.set_defaults(handler=_record)

    profile = commands.add_parser(
        "profile",
        help="profile one recorded ContextLens request (legacy trace workflow)",
    )
    profile.add_argument("trace", type=Path)
    profile.add_argument("--request-id")
    profile.add_argument("--observation", type=Path)
    profile.add_argument("--artifacts", type=Path)
    _format_arguments(profile)
    profile.set_defaults(handler=_profile)

    analyze = commands.add_parser(
        "analyze",
        help="compare paired baseline and ablated measurements",
    )
    analyze.add_argument("measurements", type=Path)
    analyze.add_argument("--baseline", required=True)
    analyze.add_argument("--ablated", required=True)
    analyze.add_argument("--label")
    analyze.add_argument("--confidence", type=float, default=0.95)
    analyze.add_argument("--bootstrap-samples", type=int, default=2_000)
    analyze.add_argument("--seed", type=int, default=0)
    analyze.add_argument("--equivalence-tolerance", type=float, default=0)
    analyze.add_argument("--runs-per-day", type=float)
    analyze.add_argument("--projection-days", type=int, default=30)
    analyze.add_argument("--experiment-cost-usd", type=float, default=0)
    _format_arguments(analyze)
    analyze.set_defaults(handler=_analyze)

    optimize = commands.add_parser(
        "optimize",
        help="run adaptive search and combined target-model verification",
    )
    optimize.add_argument("config", type=Path)
    _format_arguments(optimize)
    optimize.set_defaults(handler=_optimize)

    report = commands.add_parser(
        "report",
        help="render a saved ContextLens report",
    )
    report.add_argument("report", type=Path)
    _format_arguments(report)
    report.set_defaults(handler=_render_saved)

    policy = commands.add_parser(
        "policy",
        help="export a validated context policy from a saved report",
    )
    policy.add_argument("report", type=Path)
    policy.add_argument("--objective", default="balanced")
    policy.add_argument("--format", choices=("yaml", "json"), default="yaml")
    policy.add_argument("--output", required=True, type=Path)
    policy.set_defaults(handler=_export_policy)

    trim = commands.add_parser(
        "trim",
        help="apply a verified policy and emit prompt-ready context",
    )
    trim.add_argument("context", type=Path)
    trim.add_argument("--policy", required=True, type=Path)
    trim.add_argument("--output", type=Path)
    trim.add_argument("--lazy-output", type=Path)
    trim.add_argument("--audit-output", type=Path)
    trim.add_argument("--request-id")
    trim.add_argument("--agent-id")
    trim.add_argument("--phase")
    trim.add_argument("--max-tokens", type=int)
    trim.add_argument("--min-reduction", type=float, default=0.0)
    trim.add_argument("--strict", action="store_true")
    trim.add_argument("--dry-run", action="store_true")
    trim.add_argument("--force", action="store_true")
    trim.set_defaults(handler=_trim)
    return parser


def _format_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=("terminal", "json", "csv", "html"),
        default="terminal",
    )
    parser.add_argument("--output", type=Path)


def _record(arguments: argparse.Namespace) -> int:
    command = list(arguments.agent_command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise ValueError("record requires an agent command after --")
    output = arguments.output.resolve()
    if output.exists():
        raise ValueError(f"trace already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["CONTEXTLENS_TRACE"] = str(output)
    completed = subprocess.run(command, env=environment, check=False)
    if completed.returncode != 0:
        return completed.returncode
    if not output.exists():
        raise RuntimeError(
            "agent completed without writing CONTEXTLENS_TRACE; "
            "instrument the agent with TraceWriter"
        )
    TraceReader(output).read_header()
    print(output)
    return 0


def _scan(arguments: argparse.Namespace) -> int:
    target = arguments.target
    if target.is_file() and target.suffix.casefold() == ".jsonl":
        return _profile_trace(arguments, target)
    report = scan_repository(target)
    if arguments.format in {"csv", "html"}:
        raise ValueError(
            "repository scan supports terminal, json, or markdown; "
            "use `contextlens profile` for legacy report formats"
        )
    content = (
        render_scan_terminal(report)
        if arguments.format == "terminal"
        else json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"
        if arguments.format == "json"
        else render_markdown(report)
    )
    _write_text(content, arguments.output)
    return 0


def _profile(arguments: argparse.Namespace) -> int:
    return _profile_trace(arguments, arguments.trace)


def _profile_trace(arguments: argparse.Namespace, trace: Path) -> int:
    events = list(TraceReader(trace).events())
    selected = _select_request(events, arguments.request_id)
    observation = _observation(_load_json(arguments.observation))
    artifact_store = (
        ArtifactStore(arguments.artifacts) if arguments.artifacts is not None else None
    )
    profile = ContextProfiler(artifact_store=artifact_store).profile(
        selected,
        observation,
    )
    report = (
        ReportBuilder("ContextLens one-run profile")
        .add_profile(profile)
        .metadata(
            trace=str(trace),
            request_id=profile.request_id,
        )
        .build()
    )
    _write_report(report, arguments.format, arguments.output)
    return 0


def _diff(arguments: argparse.Namespace) -> int:
    report = diff_repository(arguments.repository, base_ref=arguments.base)
    content = (
        render_diff_terminal(report)
        if arguments.format == "terminal"
        else json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"
        if arguments.format == "json"
        else render_markdown(report)
    )
    _write_text(content, arguments.output)
    return 0


def _verify(arguments: argparse.Namespace) -> int:
    report = verify_repository(
        arguments.config,
        root=arguments.repository,
        base_ref=arguments.base,
    )
    content = (
        render_verification_terminal(report)
        if arguments.format == "terminal"
        else json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"
        if arguments.format == "json"
        else render_verification_markdown(report)
    )
    _write_text(content, arguments.output)
    return report.exit_code


def _minimize(arguments: argparse.Namespace) -> int:
    report = minimize_repository(
        arguments.repository,
        config_path=arguments.config,
        selected_paths=tuple(arguments.paths),
        max_candidates=arguments.max_candidates,
    )
    if arguments.patch_output is not None:
        if not report.recommended or report.patch is None:
            raise ValueError(
                "refusing to write a minimization patch that did not pass verification"
            )
        patch_output = arguments.patch_output.resolve()
        patch_output.parent.mkdir(parents=True, exist_ok=True)
        patch_output.write_text(report.patch, encoding="utf-8", newline="\n")
    content = (
        render_minimization_terminal(report)
        if arguments.format == "terminal"
        else json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"
    )
    _write_text(content, arguments.report_output)
    return report.exit_code


def _ci(arguments: argparse.Namespace) -> int:
    if arguments.mode == "static":
        context_diff = diff_repository(arguments.repository, base_ref=arguments.base)
        result = evaluate_static_ci(
            context_diff,
            StaticCiPolicy(
                max_context_increase_fraction=arguments.max_context_increase,
                max_duplicate_increase_tokens=arguments.max_duplicate_increase,
                max_stale_reference_increase=arguments.max_stale_increase,
            ),
        )
        summary = render_markdown(context_diff)
    else:
        verification = verify_repository(
            arguments.config,
            root=arguments.repository,
            base_ref=arguments.base,
        )
        result = evaluate_verified_ci(verification)
        summary = render_verification_markdown(verification)
    if arguments.json_output is not None:
        _write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n",
            arguments.json_output,
        )
    summary_path = arguments.summary
    if summary_path is None:
        github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        summary_path = Path(github_summary) if github_summary else None
    if summary_path is not None:
        write_summary(summary_path, summary)
    sys.stdout.write(summary)
    if result.reasons:
        for reason in result.reasons:
            print(f"contextlens ci: {reason}", file=sys.stderr)
    return result.exit_code


def _analyze(arguments: argparse.Namespace) -> int:
    value = _load_json(arguments.measurements)
    raw_items = value.get("measurements", value) if isinstance(value, dict) else value
    if not isinstance(raw_items, list):
        raise ValueError("measurements file must contain a JSON list")
    measurements = tuple(_measurement(item) for item in raw_items)
    effect = PairedAnalyzer(
        confidence=arguments.confidence,
        bootstrap_samples=arguments.bootstrap_samples,
        random_seed=arguments.seed,
        equivalence_tolerance=arguments.equivalence_tolerance,
    ).analyze(
        measurements,
        baseline_variant_id=arguments.baseline,
        ablated_variant_id=arguments.ablated,
    )
    builder = ReportBuilder("ContextLens paired analysis").add_effect(
        effect,
        source_id=arguments.ablated,
        name=arguments.label or arguments.ablated,
    )
    if arguments.runs_per_day is not None:
        recommendation = SavingsAnalyzer().recommend(
            effect,
            Workload(
                runs_per_day=arguments.runs_per_day,
                projection_days=arguments.projection_days,
                experiment_cost_usd=arguments.experiment_cost_usd,
            ),
            source_id=arguments.ablated,
            name=arguments.label or arguments.ablated,
        )
        builder.add_savings(recommendation)
    report = builder.metadata(
        baseline_variant_id=arguments.baseline,
        ablated_variant_id=arguments.ablated,
        confidence=arguments.confidence,
        equivalence_tolerance=arguments.equivalence_tolerance,
    ).build()
    _write_report(report, arguments.format, arguments.output)
    return 0


def _optimize(arguments: argparse.Namespace) -> int:
    config_path = arguments.config.resolve()
    config = _load_json(config_path)
    if not isinstance(config, dict):
        raise ValueError("optimization config must be a JSON object")
    base = config_path.parent
    trace_path = _relative_path(base, config["trace"])
    events = list(TraceReader(trace_path).events())
    selected = _select_request(events, config.get("request_id"))
    context = tuple(event.source for event in selected)
    artifacts = config.get("artifacts")
    artifact_store = (
        ArtifactStore(_relative_path(base, artifacts))
        if artifacts is not None
        else None
    )
    profile = ContextProfiler(artifact_store=artifact_store).profile(
        selected,
        _observation(config.get("observation", {})),
    )

    task_value = _object(config, "task")
    agent_value = _object(config, "agent")
    task = ReplayTask(
        task_id=str(task_value["task_id"]),
        instruction=str(task_value["instruction"]),
        metadata=dict(task_value.get("metadata", {})),
    )
    settings = AgentSettings(
        provider=str(agent_value["provider"]),
        model=str(agent_value["model"]),
        seed=_optional_int(agent_value.get("seed")),
        temperature=_optional_float(agent_value.get("temperature")),
        tools=tuple(agent_value.get("tools", ())),
        parameters=dict(agent_value.get("parameters", {})),
    )
    limits = ResourceLimits(**dict(config.get("limits", {})))
    worker = ReplayWorker(
        adapter=SubprocessAgentAdapter(
            tuple(str(item) for item in agent_value["command"]),
            adapter_id=str(agent_value.get("adapter_id", "subprocess-v1")),
        ),
        snapshot=DirectorySnapshot(_relative_path(base, task_value["workspace"])),
        task=task,
        context=context,
        settings=settings,
        timeout_seconds=limits.timeout_seconds,
    )
    coordinator = ReplayCoordinator(
        worker,
        limits,
        cache=MemoryReplayCache(),
    )
    evaluator = _evaluator(_object(config, "evaluator"), task.task_id)
    search_value = dict(config.get("search", {}))
    score_name = str(search_value.pop("score_name", "quality"))
    planner = AdaptiveAblationPlanner(
        context,
        config=SearchConfig(**search_value),
        profiles=profile.profiles,
    )
    search_run = AdaptiveSearchRunner(
        planner,
        coordinator,
        evaluator,
        score_name=score_name,
    ).run()
    baseline_result = next(
        result
        for result in search_run.replay_results
        if result.variant_id == "baseline"
        and result.status in {ReplayStatus.COMPLETED, ReplayStatus.CACHED}
    )
    baseline_evaluation = _evaluation_for_result(
        search_run.replay_results,
        search_run.evaluations,
        baseline_result.run_id,
    )
    optimization_value = dict(config.get("optimization", {}))
    objective = OptimizationObjective(optimization_value.pop("objective", "min_cost"))
    predictor_path = optimization_value.pop("predictor", None)
    predictor = (
        ContextValuePredictor.from_dict(
            _load_json(_relative_path(base, predictor_path))
        )
        if predictor_path is not None
        else None
    )
    verify_estimated_cost = optimization_value.pop(
        "verification_estimated_cost_usd",
        None,
    )
    policy = OptimizationPolicy(objective=objective, **optimization_value)
    optimizer = ContextOptimizer(
        context,
        profiles=profile.profiles,
        predictor=predictor,
    )
    candidate = optimizer.propose(search_run.report, policy)
    baseline_outcome = baseline_result.outcome
    verified = optimizer.verify(
        candidate,
        coordinator=coordinator,
        evaluator=evaluator,
        score_name=score_name,
        baseline_score=baseline_evaluation.scores[score_name],
        baseline_cost_usd=(
            baseline_outcome.cost_usd if baseline_outcome is not None else None
        ),
        baseline_latency_seconds=baseline_result.duration_seconds,
        estimated_cost_usd=_optional_float(verify_estimated_cost),
        policy=policy,
    )
    report = (
        ReportBuilder("ContextLens optimization report")
        .add_profile(profile)
        .add_search(search_run.report)
        .add_verified_configuration(verified)
        .add_runs(
            (
                *search_run.replay_results,
                verified.replay_result,
            )
        )
        .metadata(
            config=str(config_path),
            task_id=task.task_id,
            provider=settings.provider,
            model=settings.model,
            evaluator=evaluator.evaluator_id,
        )
        .build()
    )
    _write_report(report, arguments.format, arguments.output)
    return 0 if verified.accepted else 3


def _render_saved(arguments: argparse.Namespace) -> int:
    value = _load_json(arguments.report)
    if not isinstance(value, dict):
        raise ValueError("report file must contain a JSON object")
    _write_report(
        Report.from_dict(value),
        arguments.format,
        arguments.output,
    )
    return 0


def _export_policy(arguments: argparse.Namespace) -> int:
    value = _load_json(arguments.report)
    if not isinstance(value, dict):
        raise ValueError("report must contain a JSON object")
    policy = policy_from_report(
        Report.from_dict(value),
        objective=arguments.objective,
    )
    output = arguments.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    content = policy.to_yaml() if arguments.format == "yaml" else policy.to_json()
    output.write_text(content, encoding="utf-8")
    print(output)
    return 0


def _trim(arguments: argparse.Namespace) -> int:
    if arguments.max_tokens is not None and arguments.max_tokens < 0:
        raise ValueError("--max-tokens cannot be negative")
    if not 0 <= arguments.min_reduction <= 1:
        raise ValueError("--min-reduction must be between 0 and 1")
    if not arguments.dry_run and arguments.output is None:
        raise ValueError("trim requires --output unless --dry-run is used")
    try:
        policy = ContextPolicy.from_json(arguments.policy.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            "trim requires a JSON policy; export one with "
            "`contextlens policy --format json`"
        ) from error
    context = _load_context(arguments.context, arguments.request_id)
    applied = apply_context_policy(
        context,
        policy,
        agent_id=arguments.agent_id,
        phase=arguments.phase,
        strict=arguments.strict,
    )
    if arguments.max_tokens is not None and applied.after_tokens > arguments.max_tokens:
        raise ValueError(
            f"trimmed context has {applied.after_tokens} tokens, exceeding "
            f"--max-tokens {arguments.max_tokens}"
        )
    if applied.reduction_fraction < arguments.min_reduction:
        raise ValueError(
            f"context reduction {applied.reduction_fraction:.1%} is below "
            f"--min-reduction {arguments.min_reduction:.1%}"
        )
    if not arguments.dry_run:
        assert arguments.output is not None
        output = arguments.output.resolve()
        _write_json_artifact(
            output,
            applied.prompt_dict(),
            force=arguments.force,
        )
        if applied.lazy or arguments.lazy_output is not None:
            lazy_output = (
                arguments.lazy_output.resolve()
                if arguments.lazy_output is not None
                else output.with_name(f"{output.stem}.lazy.json")
            )
            _write_json_artifact(
                lazy_output,
                applied.lazy_dict(),
                force=arguments.force,
            )
        if arguments.audit_output is not None:
            _write_json_artifact(
                arguments.audit_output.resolve(),
                applied.audit_dict(),
                force=arguments.force,
            )
    print(
        f"Context: {applied.before_tokens:,} -> {applied.after_tokens:,} tokens "
        f"({applied.saved_tokens:,} saved, "
        f"{applied.reduction_fraction:.1%} reduction)"
    )
    if applied.warnings:
        print(f"Warnings: {len(applied.warnings)}", file=sys.stderr)
    return 0


def _write_report(
    report: Report,
    output_format: str,
    output: Path | None,
) -> None:
    renderers = {
        "terminal": render_terminal,
        "json": render_json,
        "csv": render_csv,
        "html": render_html,
    }
    content = renderers[output_format](report)
    if output is None:
        sys.stdout.write(content)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8", newline="\n")
    print(output)


def _write_text(content: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(content)
        return
    resolved = output.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8", newline="\n")
    print(resolved)


def _select_request(
    events: list[Any],
    request_id: str | None,
) -> tuple[Any, ...]:
    if not events:
        raise ValueError("trace contains no context events")
    selected_id = request_id or events[0].request_id
    selected = tuple(event for event in events if event.request_id == selected_id)
    if not selected:
        raise ValueError(f"request {selected_id!r} was not found in the trace")
    return selected


def _observation(value: Any) -> RunObservation:
    value = value or {}
    if not isinstance(value, dict):
        raise ValueError("observation must be a JSON object")
    return RunObservation(
        output_text=str(value.get("output_text", "")),
        accessed_source_ids=frozenset(value.get("accessed_source_ids", ())),
        commands=tuple(str(item) for item in value.get("commands", ())),
        tool_inputs=tuple(str(item) for item in value.get("tool_inputs", ())),
        changed_files=tuple(str(item) for item in value.get("changed_files", ())),
        task_text=str(value.get("task_text", "")),
        searched_queries=tuple(str(item) for item in value.get("searched_queries", ())),
    )


def _measurement(value: Any) -> Measurement:
    if not isinstance(value, dict):
        raise ValueError("each measurement must be a JSON object")
    values = dict(value)
    values["evidence_scope"] = EvidenceScope(
        values.get("evidence_scope", "target_model")
    )
    return Measurement(**values)


def _evaluator(value: dict[str, Any], task_id: str) -> Any:
    evaluator_type = value.get("type")
    if evaluator_type == "exact_match":
        return ExactMatchEvaluator(
            {task_id: str(value["expected"])},
            case_sensitive=bool(value.get("case_sensitive", False)),
        )
    if evaluator_type == "test_results":
        return TestResultsEvaluator(
            failure_markers=tuple(
                value.get("failure_markers", ("fail", "error", "timeout"))
            )
        )
    raise ValueError(f"unsupported evaluator type: {evaluator_type!r}")


def _evaluation_for_result(
    results: tuple[Any, ...],
    evaluations: tuple[Any, ...],
    run_id: str,
) -> Any:
    evaluation_index = 0
    for result in results:
        if result.status not in {ReplayStatus.COMPLETED, ReplayStatus.CACHED}:
            continue
        evaluation = evaluations[evaluation_index]
        evaluation_index += 1
        if result.run_id == run_id:
            return evaluation
    raise ValueError(f"no evaluation found for run {run_id!r}")


def _load_json(path: Path | None) -> Any:
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_context(path: Path, request_id: str | None) -> tuple[ContextSource, ...]:
    if path.suffix.casefold() == ".jsonl":
        return tuple(
            event.source
            for event in _select_request(
                list(TraceReader(path).events()),
                request_id,
            )
        )
    value = _load_json(path)
    raw_items = value.get("context") if isinstance(value, dict) else value
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("context JSON must be a nonempty list or contain `context`")
    if not all(isinstance(item, dict) for item in raw_items):
        raise ValueError("every context item must be a JSON object")
    return tuple(ContextSource.from_dict(item) for item in raw_items)


def _write_json_artifact(path: Path, value: Any, *, force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _relative_path(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else (base / path).resolve()


def _object(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise ValueError(f"{key!r} must be a JSON object")
    return result


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


if __name__ == "__main__":
    main()
