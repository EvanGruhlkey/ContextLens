"""Orchestration for direct real-repository ContextLens evaluations."""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from contextlens.evaluation_records import read_evaluation_records
from contextlens.experiments.codex_cli import DEFAULT_CODEX_COMMAND
from evals.repository_cases import acquire_repository, load_manifest, prepare_eval_case
from evals.run import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    DEFAULT_REASONING,
    FINAL_POLICIES,
    _aggregate,
    _merge_records,
    _provider_status,
    _run_case,
    _timestamp,
    _write_checksums,
)


@dataclass(frozen=True, slots=True)
class RepositoryRunOptions:
    model: str = DEFAULT_MODEL
    reasoning: str = DEFAULT_REASONING
    timeout_seconds: float = 600.0
    max_experiments: int = 2
    trials: int = 3
    output_root: Path = Path("evals/artifacts")


def run_repository_cases(
    manifest_paths: tuple[Path, ...],
    *,
    suite: str,
    options: RepositoryRunOptions,
) -> Path:
    """Run cases sequentially so the small smoke suite stays intentionally bounded."""

    if not manifest_paths:
        raise ValueError("at least one real-repository case is required")
    if options.trials < 1:
        raise ValueError("trials must be positive")
    manifests = tuple(load_manifest(path) for path in manifest_paths)
    if len({item.case_id for item in manifests}) != len(manifests):
        raise ValueError("repository case IDs must be unique")
    run_id = f"{suite}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    run_dir = options.output_root.resolve() / run_id
    run_dir.mkdir(parents=True)
    public_cases = [item.public_value() for item in manifests]
    _dump(run_dir / "case-manifest.json", public_cases)
    manifest: dict[str, Any] = {
        "schema_version": "2.0",
        "evaluation_run_id": run_id,
        "evaluation_kind": "real_repository",
        "suite": suite,
        "case_ids": [item.case_id for item in manifests],
        "comparison_groups": list(FINAL_POLICIES),
        "trials": options.trials,
        "provider": DEFAULT_PROVIDER,
        "model": options.model,
        "reasoning_level": options.reasoning,
        "started_at": _timestamp(),
        "ended_at": None,
        "status": "running",
        "fresh_process_per_invocation": True,
        "fresh_workspace_per_invocation": True,
        "response_cache_enabled": False,
        "solution_history_available_to_worker": False,
        "max_experiments_per_case": options.max_experiments,
    }
    _dump(run_dir / "manifest.json", manifest)
    outcomes = []
    try:
        manifest["provider_preflight"] = _provider_status(DEFAULT_CODEX_COMMAND)
        _dump(run_dir / "manifest.json", manifest)
        with TemporaryDirectory(prefix="contextlens-acquisition-") as temporary:
            acquisition_root = Path(temporary)
            for item in manifests:
                checkout = acquire_repository(item, acquisition_root / item.case_id)
                case = prepare_eval_case(item, checkout)
                outcome = _run_case(
                    case,
                    run_dir=run_dir,
                    evaluation_run_id=run_id,
                    trials=options.trials,
                    model=options.model,
                    reasoning=options.reasoning,
                    timeout_seconds=options.timeout_seconds,
                    max_experiments=options.max_experiments,
                )
                outcomes.append(outcome)
                workspace_source = outcome.directory / "workspace-source"
                if workspace_source.is_dir():
                    shutil.rmtree(workspace_source)
                _write_case_summary(
                    outcome.directory, item.repo, item.commit, item.task
                )
                print(
                    f"{item.case_id}: complete "
                    f"({outcome.invocation_count} invocations)",
                    flush=True,
                )
        attempted = _merge_records(run_dir, tuple(outcomes))
        aggregate = _aggregate(
            run_dir, tuple(outcomes), suite=suite, trials=options.trials
        )
        manifest.update(
            {
                "ended_at": _timestamp(),
                "status": "complete",
                "completed_cases": len(outcomes),
                "attempted_invocation_count": attempted,
                "aggregate": aggregate,
            }
        )
        _dump(run_dir / "manifest.json", manifest)
        _write_checksums(run_dir)
        return run_dir
    except Exception as error:
        manifest.update(
            {
                "ended_at": _timestamp(),
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
                "completed_cases": len(outcomes),
            }
        )
        _dump(run_dir / "manifest.json", manifest)
        raise


def _write_case_summary(case_dir: Path, repo: str, commit: str, task: str) -> None:
    records = read_evaluation_records(case_dir / "invocations.jsonl")
    by_policy = {
        name: tuple(record for record in records if record.policy == name)
        for name in FINAL_POLICIES
    }
    profile = _load(case_dir / "profile.json")
    policy = _load(case_dir / "context-policy.json")
    candidate_policy = _load(case_dir / "candidate-context-policy.json") or policy
    deployment = _load(case_dir / "deployment-decision.json")
    discovery = [record for record in records if record.policy not in FINAL_POLICIES]
    replay_workers = [
        record
        for record in discovery
        if record.policy.startswith("adaptive_")
        or record.policy == "optimizer_verification"
    ]
    experiment_tokens = sum((record.provider_input_tokens or 0) for record in discovery)
    experiment_uncached_tokens = sum(
        (record.provider_input_tokens or 0) - (record.provider_cached_tokens or 0)
        for record in discovery
    )
    context_rules = policy.get("context", {}) if isinstance(policy, dict) else {}
    candidate_rules = (
        candidate_policy.get("context", {})
        if isinstance(candidate_policy, dict)
        else {}
    )
    candidate_excluded = sum(
        isinstance(rule, dict) and rule.get("strategy") == "exclude"
        for rule in candidate_rules.values()
    )
    deployed_excluded = sum(
        isinstance(rule, dict) and rule.get("strategy") == "exclude"
        for rule in context_rules.values()
    )
    baseline = by_policy["full_context"]
    optimized = by_policy["contextlens"]
    random_record = by_policy["matched_random"]
    source_profiles = profile.get("sources", [])
    full_context_tokens = sum(
        int(item.get("token_count") or 0)
        for item in source_profiles
        if isinstance(item, dict)
    )
    retained_context_tokens = _prompt_context_tokens(optimized)
    reduction = (
        1 - retained_context_tokens / full_context_tokens
        if full_context_tokens and retained_context_tokens is not None
        else None
    )
    baseline_summary = _record_summary(baseline)
    optimized_summary = _record_summary(optimized)
    baseline_success = bool(baseline_summary["success"])
    optimized_success = bool(optimized_summary["success"])
    conclusion = (
        "win"
        if optimized_success and not baseline_success
        else "loss"
        if baseline_success and not optimized_success
        else "tie"
    )
    summary = {
        "repository": repo,
        "commit": commit,
        "task": task.splitlines()[0],
        "baseline": baseline_summary,
        "contextlens_discovery": {
            "context_sources_observed": len(source_profiles),
            "replay_experiments": len(replay_workers),
            "experiment_input_tokens": experiment_tokens,
            "experiment_uncached_input_tokens": experiment_uncached_tokens,
            "selected_candidate_exclusions": candidate_excluded,
        },
        "optimized_verification": {
            **optimized_summary,
            "context_reduction_fraction": reduction,
        },
        "matched_random": _record_summary(random_record),
        "deployment": {
            "accepted": bool(deployment.get("accepted", False)),
            "exported_policy_exclusions": deployed_excluded,
            "rejection_reasons": list(deployment.get("rejection_reasons", [])),
        },
        "conclusion": conclusion,
        "production_input_tokens_saved": _difference(baseline, optimized),
        "production_uncached_input_tokens_saved": _uncached_difference(
            baseline, optimized
        ),
        "experiment_break_even_runs": _break_even(
            baseline, optimized, experiment_tokens
        ),
    }
    _dump(case_dir / "case-summary.json", summary)
    (case_dir / "case-summary.txt").write_text(
        _render_summary(summary), encoding="utf-8"
    )


def _record_summary(records: Any) -> dict[str, Any]:
    values = _as_records(records)
    if not values:
        return {
            "success": False,
            "success_rate": 0.0,
            "trials": 0,
            "tests": "missing",
            "input_tokens": None,
            "latency": None,
        }
    successes = [_success(record) for record in values]
    verifications = [
        record.metadata.get("provider_evidence", {}).get("verification", {})
        for record in values
    ]
    return {
        "success": all(successes),
        "success_rate": sum(successes) / len(successes),
        "trials": len(values),
        "tests": (
            "passed" if all(item.get("passed") for item in verifications) else "failed"
        ),
        "input_tokens": _mean_optional(values, "provider_input_tokens"),
        "cached_input_tokens": _mean_optional(values, "provider_cached_tokens"),
        "output_tokens": _mean_optional(values, "provider_output_tokens"),
        "latency": sum(record.latency_seconds for record in values) / len(values),
        "tool_calls": sum(len(record.tool_calls) for record in values) / len(values),
        "files_changed": sorted(
            {path for record in values for path in record.changed_files}
        ),
    }


def _success(record: Any) -> bool:
    if record is None or not isinstance(record.parsed_score, dict):
        return False
    scores = record.parsed_score.get("scores", {})
    return bool(scores.get("success", record.parsed_score.get("success", False)))


def _difference(baseline: Any, optimized: Any) -> float | None:
    baseline_mean = _mean_optional(_as_records(baseline), "provider_input_tokens")
    optimized_mean = _mean_optional(_as_records(optimized), "provider_input_tokens")
    if baseline_mean is None or optimized_mean is None:
        return None
    return baseline_mean - optimized_mean


def _break_even(baseline: Any, optimized: Any, overhead: int) -> int | None:
    saved = _difference(baseline, optimized)
    if saved is None or saved <= 0:
        return None
    return max(1, math.ceil(overhead / saved))


def _uncached_difference(baseline: Any, optimized: Any) -> float | None:
    baseline_values = _as_records(baseline)
    optimized_values = _as_records(optimized)
    if not baseline_values or not optimized_values:
        return None
    baseline_uncached = [
        record.provider_input_tokens - record.provider_cached_tokens
        for record in baseline_values
        if record.provider_input_tokens is not None
        and record.provider_cached_tokens is not None
    ]
    optimized_uncached = [
        record.provider_input_tokens - record.provider_cached_tokens
        for record in optimized_values
        if record.provider_input_tokens is not None
        and record.provider_cached_tokens is not None
    ]
    if (
        len(baseline_uncached) != len(baseline_values)
        or len(optimized_uncached) != len(optimized_values)
    ):
        return None
    return sum(baseline_uncached) / len(baseline_uncached) - sum(
        optimized_uncached
    ) / len(optimized_uncached)


def _prompt_context_tokens(records: Any) -> float | None:
    values = _as_records(records)
    if not values:
        return None
    totals = []
    for record in values:
        included = set(record.included_context_sources)
        totals.append(
            sum(
                int(item.token_count or 0)
                for item in record.context_manifest
                if item.source_id in included
            )
        )
    return sum(totals) / len(totals)


def _as_records(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _mean_optional(records: tuple[Any, ...], field: str) -> float | None:
    values = [getattr(record, field) for record in records]
    if not values or any(value is None for value in values):
        return None
    return sum(float(value) for value in values) / len(values)


def _render_summary(value: dict[str, Any]) -> str:
    baseline = value["baseline"]
    discovery = value["contextlens_discovery"]
    optimized = value["optimized_verification"]
    random_value = value["matched_random"]
    deployment = value["deployment"]
    reduction = optimized["context_reduction_fraction"]
    reduction_text = "n/a" if reduction is None else f"{reduction:.1%}"
    return (
        f"Repository: {value['repository']}\nCommit: {value['commit']}\n"
        f"Task: {value['task']}\n\nBaseline:\n- Success: {baseline['success']}\n"
        f"- Success rate: {baseline['success_rate']:.1%} "
        f"({baseline['trials']} trials)\n"
        f"- Tests: {baseline['tests']}\n- Input tokens: {baseline['input_tokens']}\n"
        f"- Latency: {baseline['latency']}\n\nContextLens discovery:\n"
        f"- Context sources observed: {discovery['context_sources_observed']}\n"
        f"- Replay experiments: {discovery['replay_experiments']}\n"
        f"- Experiment tokens: {discovery['experiment_input_tokens']}\n"
        f"- Experiment uncached tokens: "
        f"{discovery['experiment_uncached_input_tokens']}\n"
        f"- Selected candidate exclusions: "
        f"{discovery['selected_candidate_exclusions']}\n\n"
        f"Optimized verification:\n- Success: {optimized['success']}\n"
        f"- Success rate: {optimized['success_rate']:.1%} "
        f"({optimized['trials']} trials)\n"
        f"- Tests: {optimized['tests']}\n- Input tokens: {optimized['input_tokens']}\n"
        f"- Latency: {optimized['latency']}\n- Context reduction: {reduction_text}\n\n"
        f"Matched random:\n- Success: {random_value['success']}\n"
        f"- Success rate: {random_value['success_rate']:.1%} "
        f"({random_value['trials']} trials)\n"
        f"- Tests: {random_value['tests']}\n"
        f"- Input tokens: {random_value['input_tokens']}\n"
        f"- Latency: {random_value['latency']}\n\nConclusion:\n"
        f"- ContextLens {value['conclusion']}\n"
        f"- Deployment accepted: {deployment['accepted']}\n"
        f"- Exported policy exclusions: "
        f"{deployment['exported_policy_exclusions']}\n"
        f"- Deployment rejection reasons: {deployment['rejection_reasons']}\n"
        f"- Production input-token savings: {value['production_input_tokens_saved']}\n"
        f"- Production uncached-input savings: "
        f"{value['production_uncached_input_tokens_saved']}\n"
        f"- Experiment break-even runs: {value['experiment_break_even_runs']}\n"
    )


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
