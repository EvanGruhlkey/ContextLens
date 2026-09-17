"""Conservative reporting for four-condition repository evaluations."""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from typing import Any

from benchmarks.comprehensive import POLICIES


def total_tokens(rows: list[dict[str, Any]]) -> int | None:
    if not rows or any(
        r.get("input_tokens") is None or r.get("output_tokens") is None for r in rows
    ):
        return None
    return sum(r["input_tokens"] + r["output_tokens"] for r in rows)


def compare(
    rows: list[dict[str, Any]], reference: str, candidate: str
) -> dict[str, Any]:
    groups: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        groups[(row["case"], row["trial"])][row["policy"]] = row
    pairs = [
        (group[reference], group[candidate])
        for group in groups.values()
        if reference in group
        and candidate in group
        and group[reference]["status"] == "completed"
        and group[candidate]["status"] == "completed"
    ]
    before = [p[0] for p in pairs]
    after = [p[1] for p in pairs]
    reference_tokens, candidate_tokens = total_tokens(before), total_tokens(after)
    task_differences: dict[str, list[int]] = defaultdict(list)
    regressions, improvements = [], []
    for first, second in pairs:
        difference = int(second["verification"]["success"]) - int(
            first["verification"]["success"]
        )
        task_differences[first["case"]].append(difference)
        if difference:
            outcome = {"case": first["case"], "trial": first["trial"]}
            (regressions if difference < 0 else improvements).append(outcome)
    task_means = [statistics.mean(v) for v in task_differences.values()]
    rng = random.Random(731)
    bootstrap = (
        sorted(
            statistics.mean(rng.choices(task_means, k=len(task_means)))
            for _ in range(2000)
        )
        if task_means
        else []
    )
    return {
        "reference": reference,
        "candidate": candidate,
        "complete_pairs": len(pairs),
        "paired_tasks": len(task_means),
        "paired_success_difference": statistics.mean(task_means)
        if task_means
        else None,
        "task_cluster_bootstrap_95_interval": [bootstrap[49], bootstrap[1949]]
        if bootstrap
        else None,
        "observed_regressions": regressions,
        "observed_improvements": improvements,
        "paired_reference_tokens": reference_tokens,
        "paired_candidate_tokens": candidate_tokens,
        "matched_provider_token_reduction": 1 - candidate_tokens / reference_tokens
        if reference_tokens and candidate_tokens is not None
        else None,
        "quality_preservation_claim": None,
    }


def analyze(report: dict[str, Any]) -> dict[str, Any]:
    rows = report["rows"]
    expected = len(report["protocol"]["manifests"]) * report["protocol"]["trials"]
    conditions = {}
    for policy in POLICIES:
        selected = [r for r in rows if r["policy"] == policy]
        cached_known = bool(selected) and all(
            r.get("cached_input_tokens") is not None for r in selected
        )
        input_known = bool(selected) and all(
            r.get("input_tokens") is not None for r in selected
        )
        conditions[policy] = {
            "planned": expected,
            "attempted": len(selected),
            "completed": sum(r["status"] == "completed" for r in selected),
            "passed": sum(
                r["status"] == "completed" and r["verification"]["success"]
                for r in selected
            ),
            "timeouts": sum(r["status"] == "timeout" for r in selected),
            "invalid": sum(
                r["status"] not in {"completed", "timeout"} for r in selected
            ),
            "total_provider_tokens": total_tokens(selected),
            "input_tokens": sum(r["input_tokens"] for r in selected)
            if input_known
            else None,
            "output_tokens": sum(r["output_tokens"] for r in selected)
            if selected and all(r.get("output_tokens") is not None for r in selected)
            else None,
            "cached_input_tokens": sum(r["cached_input_tokens"] for r in selected)
            if cached_known
            else None,
            "uncached_input_tokens": sum(
                r["input_tokens"] - r["cached_input_tokens"] for r in selected
            )
            if cached_known and input_known
            else None,
            "median_end_to_end_seconds": statistics.median(
                r["wall_seconds"] for r in selected
            )
            if selected
            else None,
            "live_evidence_calls": sum(r["live_evidence_calls"] for r in selected),
            "snapshot_expansions": sum(r["snapshot_expansions"] for r in selected),
        }
    comparisons = [compare(rows, "normal", p) for p in POLICIES if p != "normal"]
    comparisons += [compare(rows, "full", p) for p in ("lexical", "dependency")]
    complete = all(
        c["attempted"] == expected and c["invalid"] == 0 and c["timeouts"] == 0
        for c in conditions.values()
    )
    return {
        "conditions": conditions,
        "comparisons": comparisons,
        "protocol_complete_without_invalid_or_timeout_runs": complete,
        "quality_preservation_claim": None,
        "interval_caveat": "Task-cluster bootstrap on a convenience sample; public "
        "tasks may be contaminated and tasks from the same repo remain correlated.",
        "dollar_savings_claim": None,
    }


def readme_table(analysis: dict[str, Any]) -> str:
    labels = {
        "normal": "Normal tools",
        "full": "ContextLens full files",
        "lexical": "Lexical compression",
        "dependency": "Dependency compression",
    }
    lines = [
        "| Policy | Passed / attempted | Total model tokens | Median time | "
        "Invalid | Timeouts |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for policy, c in analysis["conditions"].items():
        tokens = c["total_provider_tokens"]
        timing = c["median_end_to_end_seconds"]
        lines.append(
            f"| {labels[policy]} | {c['passed']} / {c['attempted']} | "
            + (f"{tokens:,}" if tokens is not None else "Unknown")
            + " | "
            + (f"{timing:.1f}s" if timing is not None else "Unknown")
            + f" | {c['invalid']} | {c['timeouts']} |"
        )
    return "\n".join(lines)
