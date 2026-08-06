"""Validate integrity and completeness of a real ContextLens evaluation run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from contextlens.analysis import Measurement, PairedAnalyzer
from contextlens.evaluation_records import read_evaluation_records
from contextlens.trace import TraceReader
from evals.cases import EvalSuite, get_suite
from evals.run import FINAL_POLICIES, suite_manifest


class ValidationFailure(RuntimeError):
    """Raised when a run cannot support its reported conclusions."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        message = f"cannot read valid JSON from {path}: {error}"
        raise ValidationFailure(message) from error


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationFailure(message)


def _latest_run(root: Path) -> Path:
    candidates = tuple(path for path in root.glob("*") if path.is_dir())
    if not candidates:
        raise ValidationFailure(f"no evaluation runs found under {root}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _validate_checksums(run_dir: Path) -> None:
    checksums = _load_json(run_dir / "checksums.json")
    _require(isinstance(checksums, dict), "checksums.json must be an object")
    actual_paths = {
        path.relative_to(run_dir).as_posix()
        for path in run_dir.rglob("*")
        if path.is_file() and path.name != "checksums.json"
    }
    _require(
        set(checksums) == actual_paths,
        "checksum manifest does not exactly cover all run artifacts",
    )
    for relative, expected in checksums.items():
        actual = hashlib.sha256((run_dir / relative).read_bytes()).hexdigest()
        _require(actual == expected, f"checksum mismatch: {relative}")


def _policy_exclusions(case_dir: Path) -> set[str]:
    policy = _load_json(case_dir / "context-policy.json")
    rules = policy.get("context") if isinstance(policy, dict) else None
    if not isinstance(rules, dict):
        raise ValidationFailure(f"invalid context policy in {case_dir}")
    return {
        str(rule["source_id"])
        for rule in rules.values()
        if isinstance(rule, dict) and rule.get("strategy") == "exclude"
    }


def _measurement(record: Any) -> Measurement:
    parsed = record.parsed_score
    if not isinstance(parsed, dict):
        raise ValidationFailure("control record lacks parsed score")
    scores = parsed.get("scores")
    if not isinstance(scores, dict):
        raise ValidationFailure("control record lacks score mapping")
    _require("quality" in scores, "control record lacks quality score")
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


def _same_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)


def validate_run(run_dir: Path) -> dict[str, Any]:
    """Validate one completed run and return a concise audit summary."""

    run_dir = run_dir.resolve()
    manifest = _load_json(run_dir / "manifest.json")
    _require(isinstance(manifest, dict), "manifest.json must be an object")
    _require(manifest.get("schema_version") == "1.0", "unsupported run schema")
    _require(manifest.get("status") == "complete", "evaluation run is not complete")
    _require(manifest.get("fresh_process_per_invocation") is True, "fresh flag absent")
    _require(manifest.get("conversation_reuse") is False, "conversation reuse enabled")
    _require(
        manifest.get("response_cache_enabled") is False,
        "response cache must be disabled",
    )
    _require(manifest.get("retries") == 0, "evaluation retries must be zero")
    suite = EvalSuite(str(manifest.get("suite")))
    trials = int(manifest.get("trials", 0))
    cases = get_suite(suite)
    if suite is EvalSuite.HELDOUT:
        _require(len(cases) >= 20, "heldout suite has fewer than 20 cases")
        _require(trials >= 3, "heldout suite has fewer than 3 trials")
    expected_manifest, expected_hash = suite_manifest(cases)
    _require(
        manifest.get("case_manifest_hash") == expected_hash,
        "current case corpus does not match the frozen run hash",
    )
    _require(
        _load_json(run_dir / "case-manifest.json") == expected_manifest,
        "case-manifest.json does not match the current frozen corpus",
    )
    case_ids = tuple(case.case_id for case in cases)
    _require(
        tuple(manifest.get("case_ids", ())) == case_ids,
        "manifest case ordering or membership is wrong",
    )
    _require(
        tuple(manifest.get("final_policies", ())) == FINAL_POLICIES,
        "required final policy comparison set is incomplete",
    )
    records = read_evaluation_records(run_dir / "invocations.jsonl")
    _require(bool(records), "invocation audit log is empty")
    _require(
        len(records) == int(manifest.get("attempted_invocation_count", -1)),
        "manifest invocation count does not match JSONL records",
    )
    replay_ids = [str(record.metadata.get("replay_run_id")) for record in records]
    _require(len(replay_ids) == len(set(replay_ids)), "replay run IDs were reused")
    workspaces = [record.workspace_id for record in records]
    _require(len(workspaces) == len(set(workspaces)), "workspace IDs were reused")
    threads: list[str] = []
    provider = str(manifest["provider"])
    model = str(manifest["model"])
    for record in records:
        _require(
            record.evaluation_run_id == manifest["evaluation_run_id"],
            "run ID drift",
        )
        _require(
            record.case_id in case_ids,
            f"unknown case in record: {record.case_id}",
        )
        _require(record.provider == provider, "provider drift between invocations")
        _require(record.model == model, "model drift between invocations")
        _require(record.retry_count == 0, "a model invocation was retried")
        _require(record.status != "cached", "a cached response was used")
        _require(bool(record.task_prompt), "missing task prompt")
        _require(bool(record.rendered_prompt), "missing exact rendered prompt")
        _require(
            record.rendered_prompt.count("<task task_id=") == 1,
            "an invocation prompt does not contain exactly one task",
        )
        _require(
            f'<task task_id="{record.case_id}">' in record.rendered_prompt,
            "invocation prompt task ID does not match its case",
        )
        evidence = record.metadata.get("provider_evidence")
        if not isinstance(evidence, dict):
            raise ValidationFailure("missing raw provider evidence")
        raw_jsonl = evidence.get("raw_jsonl")
        _require(
            isinstance(raw_jsonl, str) and bool(raw_jsonl),
            "missing raw provider JSONL",
        )
        thread_id = evidence.get("thread_id")
        if record.status == "completed":
            _require(
                record.provider_input_tokens is not None,
                "completed invocation lacks provider input tokens",
            )
            _require(
                record.provider_output_tokens is not None,
                "completed invocation lacks provider output tokens",
            )
            _require(
                isinstance(thread_id, str) and bool(thread_id),
                "completed invocation lacks a provider thread ID",
            )
        if isinstance(thread_id, str) and thread_id:
            threads.append(thread_id)
        _require(
            record.metadata.get("response_source") == "fresh_codex_exec_jsonl",
            "record does not identify a fresh provider response",
        )
    _require(len(threads) == len(set(threads)), "provider thread IDs were reused")

    by_case_policy: dict[tuple[str, str], list[int]] = defaultdict(list)
    roles: Counter[str] = Counter()
    policies: Counter[str] = Counter()
    for record in records:
        roles[record.role.value] += 1
        policies[record.policy] += 1
        if record.policy in FINAL_POLICIES:
            by_case_policy[(record.case_id, record.policy)].append(record.trial)
    for case in cases:
        case_dir = run_dir / "cases" / case.case_id
        result = _load_json(case_dir / "result.json")
        _require(result.get("status") == "complete", f"case failed: {case.case_id}")
        _require((case_dir / "report.json").is_file(), "missing production JSON report")
        _require((case_dir / "report.html").is_file(), "missing production HTML report")
        _require((case_dir / "report.txt").is_file(), "missing terminal report")
        _require((case_dir / "profile.json").is_file(), "missing profile artifact")
        _require(
            (case_dir / "candidate-plan.json").is_file(),
            "missing production candidate plan",
        )
        _require(
            (case_dir / "adaptive-search.json").is_file(),
            "missing adaptive search artifact",
        )
        adaptive = _load_json(case_dir / "adaptive-search.json")
        _require(isinstance(adaptive, dict), "adaptive search must be an object")
        adaptive_report = adaptive.get("report")
        adaptive_effects = adaptive.get("effects")
        _require(isinstance(adaptive_report, dict), "adaptive report is missing")
        _require(isinstance(adaptive_effects, list), "adaptive effects are missing")
        nodes = adaptive_report.get("nodes")
        _require(isinstance(nodes, list), "adaptive coordinator nodes are missing")
        coordinator_variants = {
            str(node["variant_id"])
            for node in nodes
            if isinstance(node, dict) and node.get("variant_id") is not None
        }
        analyzed_variants = {
            str(effect["ablated_variant_id"])
            for effect in adaptive_effects
            if isinstance(effect, dict) and effect.get("ablated_variant_id")
        }
        adaptive_records = tuple(
            record
            for record in records
            if record.case_id == case.case_id
            and record.policy == "adaptive_ablation"
        )
        tested_variants = {
            str(record.intervention_id)
            for record in adaptive_records
            if record.intervention_id is not None
        }
        adaptive_baselines = tuple(
            record
            for record in records
            if record.case_id == case.case_id
            and record.policy == "adaptive_baseline"
            and record.intervention_id in tested_variants
        )
        _require(
            tested_variants <= coordinator_variants,
            f"{case.case_id} replayed an intervention not requested by coordinator",
        )
        _require(
            tested_variants == analyzed_variants,
            f"{case.case_id} adaptive replay omitted production paired analysis",
        )
        for variant in tested_variants:
            variant_ablations = tuple(
                record
                for record in adaptive_records
                if record.intervention_id == variant
            )
            variant_baselines = tuple(
                record
                for record in adaptive_baselines
                if record.intervention_id == variant
            )
            _require(
                len(variant_ablations) == trials
                and len(variant_baselines) == trials,
                f"{case.case_id}/{variant} lacks exactly {trials} attempts per side",
            )
            _require(
                all(
                    record.status == "completed" or bool(record.error)
                    for record in (*variant_baselines, *variant_ablations)
                ),
                f"{case.case_id}/{variant} silently omitted a failed attempt",
            )
            successful_pairs = len(
                {
                    record.trial
                    for record in variant_baselines
                    if record.status == "completed"
                }
                & {
                    record.trial
                    for record in variant_ablations
                    if record.status == "completed"
                }
            )
            matching_effects = [
                effect
                for effect in adaptive_effects
                if isinstance(effect, dict)
                and effect.get("ablated_variant_id") == variant
            ]
            _require(
                len(matching_effects) == 1
                and matching_effects[0].get("pair_count") == successful_pairs,
                f"{case.case_id}/{variant} paired-analysis count is wrong",
            )
        traces = tuple((case_dir / "traces").glob("*.jsonl"))
        _require(len(traces) == 1, "each case must contain exactly one baseline trace")
        reader = TraceReader(traces[0])
        reader.read_header()
        _require(bool(tuple(reader.events())), "baseline trace has no context events")
        _require(bool(tuple(reader.steps())), "baseline trace has no execution steps")
        exclusions = _policy_exclusions(case_dir)
        policy_value = _load_json(case_dir / "context-policy.json")
        policy_rules = policy_value.get("context")
        if not isinstance(policy_rules, dict):
            raise ValidationFailure("context policy rules are missing")
        verification_records = tuple(
            record
            for record in records
            if record.case_id == case.case_id
            and record.policy == "optimizer_verification"
        )
        _require(
            len(verification_records) == 1,
            f"{case.case_id} lacks one optimizer verification record",
        )
        verification_run_id = str(
            verification_records[0].metadata.get("replay_run_id")
        )
        _require(
            bool(verification_run_id) and verification_run_id != "None",
            f"{case.case_id} optimizer verification lacks replay ID",
        )
        _require(
            all(
                isinstance(rule, dict)
                and rule.get("verification_run_id") == verification_run_id
                for rule in policy_rules.values()
            ),
            f"{case.case_id} final policy was not produced by verified optimizer",
        )
        for policy in FINAL_POLICIES:
            observed = sorted(by_case_policy[(case.case_id, policy)])
            _require(
                observed == list(range(1, trials + 1)),
                f"{case.case_id}/{policy} lacks exactly {trials} fresh trials",
            )
        contextlens_records = tuple(
            record
            for record in records
            if record.case_id == case.case_id and record.policy == "contextlens"
        )
        for record in contextlens_records:
            _require(
                set(record.excluded_context_sources) == exclusions,
                f"{case.case_id} ContextLens replay does not match exported policy",
            )
            _require(
                record.parent_run_id == verification_run_id,
                f"{case.case_id} ContextLens control is not linked to verification",
            )
        _require(
            policies["profiler_baseline"] >= len(cases),
            "missing profiler baselines",
        )
    expected_controls = len(cases) * trials * len(FINAL_POLICIES)
    observed_controls = sum(policies[name] for name in FINAL_POLICIES)
    _require(
        observed_controls == expected_controls,
        "final comparison invocation count is incomplete",
    )
    _require(
        policies["optimizer_verification"] == len(cases),
        "each case needs exactly one fresh optimizer verification",
    )
    _require(
        policies["adaptive_ablation"] >= len(cases) * trials,
        "adaptive replay trials are incomplete",
    )
    aggregate = _load_json(run_dir / "aggregate.json")
    _require(
        aggregate.get("measurement_count") == expected_controls,
        "aggregate omits final comparison measurements",
    )
    effects = aggregate.get("paired_effects_vs_full_context")
    _require(isinstance(effects, dict), "aggregate paired effects are missing")
    _require(
        set(effects) == set(FINAL_POLICIES[1:]),
        "aggregate paired effects omit a control policy",
    )
    final_measurements = tuple(
        _measurement(record)
        for record in records
        if record.policy in FINAL_POLICIES
    )
    analyzer = PairedAnalyzer(equivalence_tolerance=0.02)
    numeric_fields = (
        "baseline_mean",
        "ablated_mean",
        "effect",
        "confidence_low",
        "confidence_high",
        "baseline_success_rate",
        "ablated_success_rate",
        "success_rate_effect",
        "input_tokens_saved_by_ablation",
        "output_tokens_saved_by_ablation",
        "latency_saved_by_ablation_seconds",
        "tool_calls_saved_by_ablation",
    )
    for policy in FINAL_POLICIES[1:]:
        reconstructed = analyzer.analyze(
            final_measurements,
            baseline_variant_id="full_context",
            ablated_variant_id=policy,
        )
        stored = effects[policy]
        _require(isinstance(stored, dict), f"aggregate effect missing: {policy}")
        _require(
            stored.get("pair_count") == reconstructed.pair_count
            and stored.get("task_count") == reconstructed.task_count
            and stored.get("verdict") == reconstructed.verdict.value,
            f"aggregate paired identity cannot be reconstructed: {policy}",
        )
        for field in numeric_fields:
            _require(
                _same_number(stored.get(field), getattr(reconstructed, field)),
                f"aggregate field cannot be reconstructed: {policy}/{field}",
            )
    _validate_checksums(run_dir)
    return {
        "run_dir": str(run_dir),
        "evaluation_run_id": manifest["evaluation_run_id"],
        "suite": suite.value,
        "cases": len(cases),
        "trials": trials,
        "invocations": len(records),
        "unique_workspaces": len(set(workspaces)),
        "unique_provider_threads": len(set(threads)),
        "role_counts": dict(sorted(roles.items())),
        "policy_counts": dict(sorted(policies.items())),
        "status": "valid",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, nargs="?")
    parser.add_argument("--artifacts-root", type=Path, default=Path("evals/artifacts"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run_dir = args.run_dir or _latest_run(args.artifacts_root.resolve())
        summary = validate_run(run_dir)
    except (OSError, ValidationFailure, ValueError) as error:
        print(f"INVALID EVALUATION: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
