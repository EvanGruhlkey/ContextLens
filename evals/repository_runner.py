"""Orchestration for direct real-repository ContextLens evaluations."""

from __future__ import annotations

import json
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
        "trials": 1,
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
                    trials=1,
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
        aggregate = _aggregate(run_dir, tuple(outcomes), suite=suite, trials=1)
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
        name: next((record for record in records if record.policy == name), None)
        for name in FINAL_POLICIES
    }
    profile = _load(case_dir / "profile.json")
    policy = _load(case_dir / "context-policy.json")
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
    excluded = sum(
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
    baseline_success = _success(baseline)
    optimized_success = _success(optimized)
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
        "baseline": _record_summary(baseline),
        "contextlens_discovery": {
            "context_sources_observed": len(source_profiles),
            "replay_experiments": len(replay_workers),
            "experiment_input_tokens": experiment_tokens,
            "experiment_uncached_input_tokens": experiment_uncached_tokens,
            "selected_policy_exclusions": excluded,
        },
        "optimized_verification": {
            **_record_summary(optimized),
            "context_reduction_fraction": reduction,
        },
        "matched_random": _record_summary(random_record),
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


def _record_summary(record: Any) -> dict[str, Any]:
    if record is None:
        return {
            "success": False,
            "tests": "missing",
            "input_tokens": None,
            "latency": None,
        }
    verification = record.metadata.get("provider_evidence", {}).get("verification", {})
    return {
        "success": _success(record),
        "tests": "passed" if verification.get("passed") else "failed",
        "input_tokens": record.provider_input_tokens,
        "cached_input_tokens": record.provider_cached_tokens,
        "output_tokens": record.provider_output_tokens,
        "latency": record.latency_seconds,
        "tool_calls": len(record.tool_calls),
        "files_changed": list(record.changed_files),
    }


def _success(record: Any) -> bool:
    if record is None or not isinstance(record.parsed_score, dict):
        return False
    scores = record.parsed_score.get("scores", {})
    return bool(scores.get("success", record.parsed_score.get("success", False)))


def _difference(baseline: Any, optimized: Any) -> int | None:
    if baseline is None or optimized is None:
        return None
    if (
        baseline.provider_input_tokens is None
        or optimized.provider_input_tokens is None
    ):
        return None
    return int(baseline.provider_input_tokens) - int(optimized.provider_input_tokens)


def _break_even(baseline: Any, optimized: Any, overhead: int) -> int | None:
    saved = _difference(baseline, optimized)
    if saved is None or saved <= 0:
        return None
    return max(1, (overhead + saved - 1) // saved)


def _uncached_difference(baseline: Any, optimized: Any) -> int | None:
    if baseline is None or optimized is None:
        return None
    values = (
        baseline.provider_input_tokens,
        baseline.provider_cached_tokens,
        optimized.provider_input_tokens,
        optimized.provider_cached_tokens,
    )
    if any(value is None for value in values):
        return None
    return int(values[0]) - int(values[1]) - int(values[2]) + int(values[3])


def _prompt_context_tokens(record: Any) -> int | None:
    if record is None:
        return None
    included = set(record.included_context_sources)
    return sum(
        int(item.token_count or 0)
        for item in record.context_manifest
        if item.source_id in included
    )


def _render_summary(value: dict[str, Any]) -> str:
    baseline = value["baseline"]
    discovery = value["contextlens_discovery"]
    optimized = value["optimized_verification"]
    random_value = value["matched_random"]
    reduction = optimized["context_reduction_fraction"]
    reduction_text = "n/a" if reduction is None else f"{reduction:.1%}"
    return (
        f"Repository: {value['repository']}\nCommit: {value['commit']}\n"
        f"Task: {value['task']}\n\nBaseline:\n- Success: {baseline['success']}\n"
        f"- Tests: {baseline['tests']}\n- Input tokens: {baseline['input_tokens']}\n"
        f"- Latency: {baseline['latency']}\n\nContextLens discovery:\n"
        f"- Context sources observed: {discovery['context_sources_observed']}\n"
        f"- Replay experiments: {discovery['replay_experiments']}\n"
        f"- Experiment tokens: {discovery['experiment_input_tokens']}\n"
        f"- Experiment uncached tokens: "
        f"{discovery['experiment_uncached_input_tokens']}\n"
        f"- Selected policy exclusions: {discovery['selected_policy_exclusions']}\n\n"
        f"Optimized verification:\n- Success: {optimized['success']}\n"
        f"- Tests: {optimized['tests']}\n- Input tokens: {optimized['input_tokens']}\n"
        f"- Latency: {optimized['latency']}\n- Context reduction: {reduction_text}\n\n"
        f"Matched random:\n- Success: {random_value['success']}\n"
        f"- Tests: {random_value['tests']}\n"
        f"- Input tokens: {random_value['input_tokens']}\n"
        f"- Latency: {random_value['latency']}\n\nConclusion:\n"
        f"- ContextLens {value['conclusion']}\n"
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
