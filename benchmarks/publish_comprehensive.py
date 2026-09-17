"""Publish measured reports and a README table without inventing missing runs."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
from pathlib import Path
from typing import Any

from benchmarks.comprehensive import POLICIES
from benchmarks.comprehensive_analysis import analyze, readme_table


def task_table(report: dict[str, Any]) -> str:
    lines = [
        "| Repository / task | Normal | Full files | Lexical | Dependency |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for manifest in report["protocol"]["manifests"]:
        cells = [f"{manifest['repo']} / `{manifest['case_id']}`"]
        for policy in POLICIES:
            selected = [
                r
                for r in report["rows"]
                if r["case"] == manifest["case_id"] and r["policy"] == policy
            ]
            passed = sum(
                r["status"] == "completed" and r["verification"]["success"]
                for r in selected
            )
            cells.append(f"{passed}/{len(selected)}")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(report)
    # Raw console output stays local; commands and statuses remain auditable.
    for row in result["rows"]:
        for key in ("verification", "original_verification"):
            for command in row.get(key, {}).get("commands", []):
                command.pop("stdout", None)
                command.pop("stderr", None)
    for preflight in result["preflights"]:
        for command in preflight["verification"]["commands"]:
            command.pop("stdout", None)
            command.pop("stderr", None)
    encoded = json.dumps(result, ensure_ascii=False)
    for name in ("USERPROFILE", "COMPUTERNAME", "USERNAME"):
        value = os.environ.get(name)
        if value:
            encoded = encoded.replace(json.dumps(value)[1:-1], "<" + name + ">")
    return json.loads(encoded)


def detailed_benchmark_section(report: dict[str, Any], analysis: dict[str, Any]) -> str:
    protocol = report["protocol"]
    planned = len(protocol["manifests"]) * protocol["trials"] * len(POLICIES)
    complete = len(report["rows"]) == planned
    protocol_invalid = sum(
        r["status"] == "invalid_live_tools_not_exercised" for r in report["rows"]
    )
    invalid_correct = sum(
        r["status"] == "invalid_live_tools_not_exercised"
        and r["verification"]["success"]
        for r in report["rows"]
    )
    lines = [
        "## Benchmarks",
        "",
        f"**{'Completed' if complete else 'Incomplete'} expanded evaluation:** "
        f"{len(report['rows'])}/{planned} attempts across "
        f"{len(protocol['manifests'])} historical bug-fix tasks in "
        f"{len({m['repo'] for m in protocol['manifests']})} real repositories. "
        f"We allocated {protocol['trials']} repeats per task and condition, using "
        f"`{protocol['model']}` with low reasoning effort.",
        "",
        readme_table(analysis),
        "",
        "Tokens include provider-reported input (including cached input) and output "
        "across the entire attempt, including failed fixes and extra reads. Unknown "
        "usage is never counted as zero. Time includes initial retrieval, agent "
        "execution and external verification; checkout/setup time is excluded.",
        "",
        "Passes require the patch to pass the hidden task checks and the run to "
        "satisfy its tool protocol. An invalid "
        "run is not a pass; timeouts are also counted as unsuccessful attempts. "
        "Every unmodified checkout failed its checks before agent execution.",
        (
            "The evaluation stopped at the user's request. "
            f"{len(report.get('interrupted_attempts', []))} in-progress attempts "
            "were canceled; their unknown usage is excluded from the totals. "
            "Conditions contain different numbers of finished tasks."
            if report.get("status") == "stopped_by_user"
            else ""
        ),
        (
            f"{protocol_invalid} {'attempt' if protocol_invalid == 1 else 'attempts'} "
            "failed the required successful evidence "
            f"verification/read workflow; {invalid_correct} of those patches "
            "passed the code checks. They remain invalid in the table and are "
            "excluded from matched comparisons; their reported tokens remain "
            "in the totals."
            if protocol_invalid
            else ""
        ),
        "",
        "### Results by task",
        "",
        task_table(report),
        "",
        "Cells show passed / attempted runs; see the raw report for invalid-run "
        "statuses and per-task token usage.",
        "",
        "### What this tells us",
        "",
    ]
    for comparison in analysis["comparisons"]:
        if comparison["reference"] != "normal":
            continue
        reduction = comparison["matched_provider_token_reduction"]
        label = comparison["candidate"]
        change = (
            f"{reduction:.1%} fewer tokens"
            if reduction is not None and reduction >= 0
            else f"{-reduction:.1%} more tokens"
            if reduction is not None
            else "unknown usage"
        )
        lines.append(
            f"- **{label}:** {change} against normal tools over "
            f"{comparison['complete_pairs']} complete matched pairs; "
            f"{len(comparison['observed_regressions'])} pass-to-fail regressions "
            f"and {len(comparison['observed_improvements'])} fail-to-pass improvements."
        )
    lines += [
        "",
        "Full-file retrieval remains the default; compression requires explicit "
        "opt-in. These measurements do not establish quality preservation or "
        "universal savings.",
        "",
        "This is a convenience sample of public historical fixes, not a "
        "contamination-free or randomly selected benchmark. Checks cover the bug "
        "and selected regressions, not complete project test suites. Tasks often "
        "name affected symbols; these results do not establish performance on "
        "vague reports, large feature work or unseen repositories. Full-file "
        "ContextLens seeds up to three ranked files under a 30,000-source-token "
        "budget; lexical and dependency policies use 3,000. ContextLens conditions "
        "must exercise live hash verification and range reads; the normal baseline "
        "has no ContextLens server or seed context. That mandatory step adds "
        "integration overhead, so the baseline comparison includes both retrieval "
        "choices and this workflow requirement. Two attempts run concurrently. "
        "No newly trained neural model is used.",
        "This matrix evaluates deterministic retrieval and source-read integration. "
        "Optional neural pruning and long-conversation memory are separate "
        "capabilities whose end-to-end quality is not established here.",
        "",
        "No paid API calls were initiated. Agent runs consume existing subscription "
        "capacity; dollar cost and dollar savings are unknown.",
        "",
        "[Detailed results and uncertainty](docs/comprehensive-benchmark.md) · "
        "[Raw measured report](benchmarks/results/comprehensive.json) · "
        "[Reproduction instructions](benchmarks/README.md)",
        "",
    ]
    if report.get("verification_revisions"):
        lines += [
            "Verifier calibration: undocumented formatting requirements were "
            "removed for Click help and Luigi error messages. All affected attempts "
            "were rechecked uniformly; original checks and scores are retained in "
            "the raw report. Agent prompts and production source were unchanged.",
            "Offline regrading time is excluded from attempt timings; those "
            "include the original external verification step.",
            "",
        ]
    if report.get("historical_runtime_reports"):
        lines += [
            "### Earlier neural runtime measurements",
            "",
            "These archived free-Colab T4 runs used an earlier source snapshot and "
            "three files with three reads each. They measure returned-text size "
            "and runtime, rather than total agent tokens or bug-fix correctness.",
            "",
            "| Runtime | Backend failures | Returned-text reduction | First read | "
            "Warm median |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for archived in report["historical_runtime_reports"]:
            summary = archived["summary"]
            label = (
                "Default (invalid run)"
                if archived["status"] == "invalid"
                else "Experimental efficient SDPA"
            )
            lines.append(
                f"| {label} | {summary['failed_observations']}/"
                f"{summary['observations']} | "
                f"{summary['observation_reduction_fraction']:.2%} | "
                f"{summary['first_request_wall_ms'] / 1000:.2f}s | "
                f"{summary['subsequent_request_median_ms'] / 1000:.2f}s |"
            )
        lines += [
            "",
            "The default exhausted GPU memory. The experimental variant's "
            "numerical equivalence is unverified; its smaller returned text "
            "does not establish agent-quality preservation. See the "
            "[runtime audit and raw reports](docs/benchmark-audit.md).",
            "",
        ]
    return "\n".join(lines)


def benchmark_section(report: dict[str, Any], analysis: dict[str, Any]) -> str:
    protocol = report["protocol"]
    planned = len(protocol["manifests"]) * protocol["trials"] * len(POLICIES)
    label = (
        "Stopped evaluation"
        if report.get("status") == "stopped_by_user"
        else "Completed evaluation"
        if len(report["rows"]) == planned
        else "Incomplete evaluation"
    )
    results_table = [
        "| Approach | Passed / finished | Total model tokens | Median time |",
        "| --- | ---: | ---: | ---: |",
    ]
    names = {
        "normal": "Normal tools",
        "full": "ContextLens full files",
        "lexical": "Lexical compression",
        "dependency": "Dependency compression",
    }
    for policy, condition in analysis["conditions"].items():
        tokens = condition["total_provider_tokens"]
        timing = condition["median_end_to_end_seconds"]
        results_table.append(
            f"| {names[policy]} | {condition['passed']} / {condition['attempted']} | "
            + (f"{tokens:,}" if tokens is not None else "Unknown")
            + " | "
            + (f"{timing:.1f}s" if timing is not None else "Unknown")
            + " |"
        )
    lines = [
        "## Benchmarks",
        "",
        "### What we tested",
        "",
        f"**{label}:** {len(report['rows'])}/{planned} attempts finished on "
        f"{len(protocol['manifests'])} historical bug-fix tasks across AWS Powertools, "
        "Luigi, tslib, Click and responses. The agent used "
        f"`{protocol['model']}` with low reasoning effort.",
        "",
        "Each run started from a pinned repository checkout. We checked the patch "
        "with hidden tests for the bug and selected regressions. Every unmodified "
        "checkout failed its checks before the agent attempted a fix.",
        "",
        "We compared four approaches:",
        "",
        "- **Normal tools:** the agent searches and reads the repository itself.",
        "- **Full files:** ContextLens supplies up to three matching files, with a "
        "30,000-source-token budget.",
        "- **Lexical compression:** ContextLens supplies matching code sections, "
        "with a 3,000-source-token budget.",
        "- **Dependency compression:** matching sections plus their static "
        "dependencies, with the same 3,000-source-token budget.",
        "",
        "### Collected results",
        "",
        "\n".join(results_table),
        "",
        "Tokens include reported input, cached input and output across each "
        "finished attempt, including failed fixes and extra reads. Cached input "
        "is included once. Median time includes retrieval, agent execution and "
        "the original external checks; checkout and offline regrading are excluded.",
        "",
    ]
    interrupted = len(report.get("interrupted_attempts", []))
    if report.get("status") == "stopped_by_user":
        lines += [
            f"Testing stopped at the user's request before all {planned} planned "
            f"runs finished. {interrupted} in-progress attempts were canceled; "
            "their usage is unknown and is excluded from this table.",
            "",
        ]
    invalid = sum(c["invalid"] for c in analysis["conditions"].values())
    if invalid:
        invalid_correct = sum(
            r["status"] != "completed" and r["verification"]["success"]
            for r in report["rows"]
        )
        lines += [
            f"{invalid} invalid run failed the required evidence verification/read "
            f"workflow despite {invalid_correct} patch passing the code checks. "
            "It does not count as a pass. Its reported tokens remain "
            "in the table.",
            "",
        ]
    lines += [
        "### What we learned",
        "",
        "The conditions have different numbers of finished runs. To compare token "
        "usage fairly, we matched runs on the same task and trial:",
        "",
    ]
    labels = {
        "full": "Full files",
        "lexical": "Lexical compression",
        "dependency": "Dependency compression",
    }
    for comparison in analysis["comparisons"]:
        if comparison["reference"] != "normal":
            continue
        reduction = comparison["matched_provider_token_reduction"]
        change = (
            f"{abs(reduction):.1%} {'fewer' if reduction >= 0 else 'more'} tokens"
            if reduction is not None
            else "unknown token usage"
        )
        lines.append(
            f"- **{labels[comparison['candidate']]}:** {change} than normal tools "
            f"over {comparison['complete_pairs']} valid matched pairs."
        )
    normal_comparisons = [
        c for c in analysis["comparisons"] if c["reference"] == "normal"
    ]
    no_savings = all(
        c["matched_provider_token_reduction"] is not None
        and c["matched_provider_token_reduction"] <= 0
        for c in normal_comparisons
    )
    regressions = any(c["observed_regressions"] for c in normal_comparisons)
    lines += [
        "",
        (
            "**These runs do not demonstrate total token savings over normal tools.** "
            if no_savings
            else "These results do not establish universal token savings. "
        )
        + (
            "We also observed correct-to-incorrect fix regressions in matched runs. "
            if regressions
            else "These results do not establish quality preservation. "
        )
        + "Smaller source context alone does not establish cheaper or equally "
        "accurate agent execution.",
        "",
        "This is a small sample of public historical tasks using one model, not "
        "a held-out benchmark or full project test suites. ContextLens runs must "
        "verify evidence and read a range, which adds workflow overhead. Different "
        "budgets also affect the comparison. This evaluation does not establish "
        "quality for optional neural pruning or long-conversation memory.",
        "",
        "No paid API calls were started. The runs used existing subscription "
        "capacity and authorized free reset credits; dollar savings are unknown.",
        "",
    ]
    archived = report.get("historical_runtime_reports", [])
    if archived:
        lines += [
            "### Earlier free-GPU runtime test",
            "",
            "An earlier source snapshot was tested on a free Colab T4: three files, "
            "three reads each. This measures returned-text size and runtime, "
            "rather than bug-fix accuracy or total agent tokens.",
            "",
            "| Neural runtime | Backend failures | Returned-text reduction | "
            "First read | Warm median |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for runtime in archived:
            s = runtime["summary"]
            name = (
                "Default (invalid run)"
                if runtime["status"] == "invalid"
                else "Experimental efficient SDPA"
            )
            lines.append(
                f"| {name} | {s['failed_observations']}/{s['observations']} | "
                f"{s['observation_reduction_fraction']:.2%} | "
                f"{s['first_request_wall_ms'] / 1000:.2f}s | "
                f"{s['subsequent_request_median_ms'] / 1000:.2f}s |"
            )
        lines += [
            "",
            "The default ran out of GPU memory. The experimental variant's "
            "numerical equivalence and agent-quality preservation are unverified.",
            "",
        ]
    lines += [
        "[Per-task results, methods and uncertainty](docs/comprehensive-benchmark.md) "
        "· [JSON results](benchmarks/results/comprehensive.json) "
        "· [CSV results](benchmarks/results/comprehensive.csv) "
        "· [Reproduce the runs](benchmarks/README.md) "
        "· [GPU runtime audit](docs/benchmark-audit.md)",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--project", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    environment_path = args.input.parent / "environment.json"
    if environment_path.exists():
        report["environment"] = json.loads(environment_path.read_text(encoding="utf-8"))
    archived_reports = []
    for name in ("colab-t4-initial", "colab-t4-efficient"):
        path = args.project / "benchmarks" / "results" / f"{name}.json"
        if path.exists():
            archived = json.loads(path.read_text(encoding="utf-8"))
            archived_reports.append(
                {key: archived[key] for key in ("summary", "status", "commit")}
                | {"report": f"benchmarks/results/{name}.json"}
            )
    if archived_reports:
        report["historical_runtime_reports"] = archived_reports
    analysis = analyze(report)
    report["analysis"] = analysis
    project = args.project.resolve()
    results = project / "benchmarks" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "comprehensive.json").write_text(
        json.dumps(public_report(report), indent=2) + "\n", encoding="utf-8"
    )
    with (results / "comprehensive.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        fields = [
            "case",
            "repo",
            "trial",
            "policy",
            "status",
            "passed",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "wall_seconds",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow(
                {name: row.get(name) for name in fields if name != "passed"}
                | {
                    "passed": row["status"] == "completed"
                    and row["verification"]["success"]
                }
            )
    path = project / "README.md"
    original = path.read_text(encoding="utf-8")
    start, end = original.index("## Benchmarks"), original.index("## Development")
    section = benchmark_section(report, analysis)
    path.write_text(
        original[:start] + section + "\n" + original[end:], encoding="utf-8"
    )
    details = (
        "# Expanded repository benchmark\n\n"
        + detailed_benchmark_section(report, analysis).split("## Benchmarks\n\n", 1)[1]
    )
    details += "\n## Paired uncertainty\n\n"
    for c in analysis["comparisons"]:
        difference = c["paired_success_difference"]
        interval = c["task_cluster_bootstrap_95_interval"]
        uncertainty = (
            f"{difference * 100:+.1f} percentage points; task-cluster bootstrap "
            f"95% interval [{interval[0] * 100:+.1f}, {interval[1] * 100:+.1f}] "
            "percentage points"
            if difference is not None and interval is not None
            else "unknown difference and interval"
        )
        details += (
            f"- {c['candidate']} versus {c['reference']}: task-weighted success "
            f"difference {uncertainty}, from {c['complete_pairs']} valid matched "
            f"pairs across {c['paired_tasks']} tasks.\n"
        )
    details += "\nIntervals use 2,000 task resamples with analysis seed 731.\n"
    details += "\n" + analysis["interval_caveat"] + "\n"
    details += (
        "\n## Token and tool accounting\n\n"
        "| Policy | Input | Cached input subset | Uncached input | Output | "
        "Live evidence calls | Expansion requests |\n"
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n"
    )
    for policy, condition in analysis["conditions"].items():
        metrics = [
            condition[key]
            for key in (
                "input_tokens",
                "cached_input_tokens",
                "uncached_input_tokens",
                "output_tokens",
                "live_evidence_calls",
                "snapshot_expansions",
            )
        ]
        details += (
            "| "
            + policy
            + " | "
            + " | ".join(
                f"{value:,}" if value is not None else "Unknown" for value in metrics
            )
            + " |\n"
        )
    details += (
        "\nCached input is included in input, not added again. Tool-call counts "
        "include failed requests; expansion requests count calls to the snapshot "
        "expansion tool, not verified recovery successes. Provider token counts "
        "include repeated "
        "conversation context across model turns, not just unique source text.\n"
    )
    details += (
        "\nProduction source SHA-256: `" + report["protocol"]["source_sha256"] + "`.\n"
    )
    details = details.replace("(docs/", "(").replace("(benchmarks/", "(../benchmarks/")
    (project / "docs" / "comprehensive-benchmark.md").write_text(
        details, encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "attempts": len(report["rows"]),
                "complete": analysis[
                    "protocol_complete_without_invalid_or_timeout_runs"
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
