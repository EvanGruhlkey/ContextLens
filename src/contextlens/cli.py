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
from contextlens.analysis import EvidenceScope, Measurement, PairedAnalyzer
from contextlens.evaluators import ExactMatchEvaluator, TestResultsEvaluator
from contextlens.experiments import (
    AgentSettings,
    AdaptiveAblationPlanner,
    AdaptiveSearchRunner,
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
from contextlens.optimization import (
    ContextOptimizer,
    ContextValuePredictor,
    OptimizationObjective,
    OptimizationPolicy,
)
from contextlens.profiler import ContextProfiler, RunObservation
from contextlens.reports import (
    Report,
    ReportBuilder,
    render_csv,
    render_html,
    render_json,
    render_terminal,
)
from contextlens.trace import ArtifactStore, TraceReader


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
        description="Measure which context helps an AI agent.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")

    record = commands.add_parser(
        "record",
        help="run an instrumented agent that writes a ContextLens trace",
    )
    record.add_argument("--output", required=True, type=Path)
    record.add_argument("agent_command", nargs=argparse.REMAINDER)
    record.set_defaults(handler=_record)

    scan = commands.add_parser(
        "scan",
        help="profile one recorded request without another model call",
    )
    scan.add_argument("trace", type=Path)
    scan.add_argument("--request-id")
    scan.add_argument("--observation", type=Path)
    scan.add_argument("--artifacts", type=Path)
    _format_arguments(scan)
    scan.set_defaults(handler=_scan)

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
    events = list(TraceReader(arguments.trace).events())
    selected = _select_request(events, arguments.request_id)
    observation = _observation(_load_json(arguments.observation))
    artifact_store = (
        ArtifactStore(arguments.artifacts)
        if arguments.artifacts is not None
        else None
    )
    profile = ContextProfiler(artifact_store=artifact_store).profile(
        selected,
        observation,
    )
    report = (
        ReportBuilder("ContextLens one-run profile")
        .add_profile(profile)
        .metadata(
            trace=str(arguments.trace),
            request_id=profile.request_id,
        )
        .build()
    )
    _write_report(report, arguments.format, arguments.output)
    return 0


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
    report = (
        ReportBuilder("ContextLens paired analysis")
        .add_effect(
            effect,
            source_id=arguments.ablated,
            name=arguments.label or arguments.ablated,
        )
        .metadata(
            baseline_variant_id=arguments.baseline,
            ablated_variant_id=arguments.ablated,
            confidence=arguments.confidence,
            equivalence_tolerance=arguments.equivalence_tolerance,
        )
        .build()
    )
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
        snapshot=DirectorySnapshot(
            _relative_path(base, task_value["workspace"])
        ),
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
    objective = OptimizationObjective(
        optimization_value.pop("objective", "min_cost")
    )
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
            baseline_outcome.cost_usd
            if baseline_outcome is not None
            else None
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
        changed_files=tuple(
            str(item)
            for item in value.get("changed_files", ())
        ),
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
