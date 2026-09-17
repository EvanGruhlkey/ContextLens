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
        for command in row["verification"]["commands"]:
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


def benchmark_section(report: dict[str, Any], analysis: dict[str, Any]) -> str:
    protocol = report["protocol"]
    planned = len(protocol["manifests"]) * protocol["trials"] * len(POLICIES)
    complete = analysis["protocol_complete_without_invalid_or_timeout_runs"]
    lines = [
        "## Benchmarks",
        "",
        f"**{'Completed' if complete else 'Incomplete'} expanded evaluation:** "
        f"{len(report['rows'])}/{planned} attempts across "
        f"{len(protocol['manifests'])} historical bug-fix tasks in "
        f"{len({m['repo'] for m in protocol['manifests']})} real repositories. "
        f"Each task has {protocol['trials']} repeats per condition, using "
        f"`{protocol['model']}` with low reasoning effort.",
        "",
        readme_table(analysis),
        "",
        "Tokens include provider-reported input (including cached input) and output "
        "across the entire attempt, including failed fixes and extra reads. Unknown "
        "usage is never counted as zero. Time includes initial retrieval, agent "
        "execution and external verification; checkout/setup time is excluded.",
        "",
        "Passes mean the resulting patch passed the hidden task checks. An invalid "
        "run is not a pass; timeouts are also counted as unsuccessful attempts. "
        "Every unmodified checkout failed its checks before agent execution.",
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
        "and selected regressions, not complete project test suites. Full-file "
        "ContextLens seeds up to three ranked files under a 30,000-source-token "
        "budget; lexical and dependency policies use 3,000. ContextLens conditions "
        "must exercise live hash verification and range reads; the normal baseline "
        "has no ContextLens server or seed context. Two attempts run concurrently. "
        "No newly trained neural model is used.",
        "",
        "No paid API calls were initiated. Agent runs consume existing subscription "
        "capacity; dollar cost and dollar savings are unknown.",
        "",
        "[Detailed results and uncertainty](docs/comprehensive-benchmark.md) · "
        "[Raw measured report](benchmarks/results/comprehensive.json) · "
        "[Reproduction instructions](benchmarks/README.md)",
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
    report = json.loads(args.input.read_text())
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
    original = path.read_text()
    start, end = original.index("## Benchmarks"), original.index("## Development")
    section = benchmark_section(report, analysis)
    path.write_text(
        original[:start] + section + "\n" + original[end:], encoding="utf-8"
    )
    details = (
        "# Expanded repository benchmark\n\n" + section.split("## Benchmarks\n\n", 1)[1]
    )
    details += "\n## Paired uncertainty\n\n"
    for c in analysis["comparisons"]:
        details += (
            f"- {c['candidate']} versus {c['reference']}: mean paired success "
            f"difference {c['paired_success_difference']}; task-cluster bootstrap "
            f"95% interval {c['task_cluster_bootstrap_95_interval']}.\n"
        )
    details += "\n" + analysis["interval_caveat"] + "\n"
    details += (
        "\nProduction source SHA-256: `" + report["protocol"]["source_sha256"] + "`.\n"
    )
    details = details.replace("(docs/", "(").replace(
        "(benchmarks/", "(../benchmarks/"
    )
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
