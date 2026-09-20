"""Compare full-source and descriptor-first Jev evidence selection."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from benchmarks.jev_selection import run as run_selection


def compare_reports(
    full_source: dict[str, Any], two_stage: dict[str, Any]
) -> dict[str, Any]:
    full = full_source["summary"]
    staged = two_stage["summary"]
    full_cost = Decimal(str(full.get("decision_cost", full.get("gateway_cost"))))
    staged_cost = Decimal(str(staged.get("decision_cost", staged.get("gateway_cost"))))
    return {
        "evidence_pass_delta": staged["evidence_passes"] - full["evidence_passes"],
        "decision_input_reduction_percent": _reduction(
            full["decision_input_tokens"], staged["decision_input_tokens"]
        ),
        "decision_output_reduction_percent": _reduction(
            full["decision_output_tokens"], staged["decision_output_tokens"]
        ),
        "delivered_context_reduction_percent": _reduction(
            full["selection_response_tokens"],
            staged["selection_response_tokens"],
        ),
        "cost_reduction_percent": _reduction(float(full_cost), float(staged_cost)),
    }


def _reduction(baseline: float, candidate: float) -> float | None:
    if baseline == 0:
        return None
    return round((1 - candidate / baseline) * 100, 1)


def run(project: Path) -> dict[str, Any]:
    full_source = run_selection(project, selection_strategy="full_source")
    two_stage = run_selection(project, selection_strategy="two_stage")
    return {
        "benchmark": "jev_evidence_selection_strategy_comparison",
        "repository_revision": full_source["repository_revision"],
        "conditions": {
            "full_source": full_source,
            "two_stage": two_stage,
        },
        "comparison": compare_reports(full_source, two_stage),
        "limits": (
            "Fixed component cases measure evidence anchors and Jev decision cost, "
            "not coding-agent patch quality or complete trajectory cost."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/results/jev-selection-comparison.json"),
    )
    args = parser.parse_args()
    report = run(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    conditions = report["conditions"]
    passed = all(
        condition["summary"]["evidence_passes"]
        == condition["summary"]["cases"]
        for condition in conditions.values()
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
