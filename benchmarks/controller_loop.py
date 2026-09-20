"""Live integrated retention, capability-filter, and action-routing benchmark."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from contextlens.action_controller import ActionController
from contextlens.action_models import ActionKind, CandidateAction
from contextlens.control_session import ControlSession
from contextlens.observations import ObservationStore
from contextlens.retention import RetentionController
from contextlens.tool_filter import ToolFilter


def action(action_id: str, kind: ActionKind, description: str) -> CandidateAction:
    tool = {
        ActionKind.SEARCH_REPOSITORY: "context_select",
        ActionKind.READ_SOURCE: "context_read",
        ActionKind.READ_DEFERRED: "context_expand",
    }.get(kind)
    return CandidateAction(action_id, kind, description, tool)


CASES = (
    {
        "task": "Fix refresh tokens expiring earlier than configured",
        "steps": (
            {
                "focus": "Locate the unknown refresh TTL implementation",
                "expected": "search",
                "observation": None,
                "actions": [
                    action(
                        "search", ActionKind.SEARCH_REPOSITORY, "Search for refresh TTL"
                    ),
                    action(
                        "read", ActionKind.READ_SOURCE, "Read a known source handle"
                    ),
                    action("edit", ActionKind.READY_TO_EDIT, "Start editing"),
                    action("stop", ActionKind.STOP, "Stop investigation"),
                ],
            },
            {
                "focus": "Inspect the implementation at the discovered location",
                "expected": "read",
                "observation": {
                    "kind": "search_result",
                    "summary": "refresh_token is defined in src/auth.py",
                    "content": "src/auth.py:84 refresh_token",
                    "source": "context_select",
                },
                "actions": [
                    action("read", ActionKind.READ_SOURCE, "Read refresh_token"),
                    action("search", ActionKind.SEARCH_REPOSITORY, "Search again"),
                    action("edit", ActionKind.READY_TO_EDIT, "Start editing"),
                    action("stop", ActionKind.STOP, "Stop investigation"),
                ],
            },
        ),
    },
    {
        "task": "Repair a patch after the targeted refresh test failed",
        "steps": (
            {
                "focus": "Understand the new failure before changing the patch",
                "expected": "failure",
                "observation": {
                    "kind": "test_output",
                    "summary": "test_refresh_expiry failed: expected 3600, got 900",
                    "content": "AssertionError: expected 3600, got 900",
                    "source": "pytest",
                },
                "actions": [
                    action(
                        "failure", ActionKind.INSPECT_FAILURE, "Inspect failure details"
                    ),
                    action(
                        "test", ActionKind.RUN_TARGETED_TEST, "Run the same test again"
                    ),
                    action("diff", ActionKind.INSPECT_DIFF, "Inspect the current diff"),
                    action("edit", ActionKind.READY_TO_EDIT, "Continue editing"),
                ],
            },
        ),
    },
)


def _usage(step: Any) -> tuple[int, int, int, float, str]:
    decisions = (step.retention, step.tools, step.action)
    calls = sum(item.model is not None for item in decisions)
    inputs = sum(item.input_tokens or 0 for item in decisions)
    outputs = sum(item.output_tokens or 0 for item in decisions)
    latency = sum(item.latency_ms or 0 for item in decisions)
    cost = sum(Decimal(item.cost or "0") for item in decisions)
    return calls, inputs, outputs, latency, str(cost)


def run() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="contextlens-loop-") as temporary:
        root = Path(temporary)
        for case_index, case in enumerate(CASES):
            session = ControlSession(
                task=case["task"],
                store=ObservationStore(root / str(case_index)),
                actions=ActionController(),
                retention=RetentionController(),
                tools=ToolFilter(),
            )
            for index, fixture in enumerate(case["steps"]):
                if fixture["observation"]:
                    session.observe(**fixture["observation"])
                result = session.next(
                    focus=fixture["focus"], candidates=fixture["actions"]
                )
                calls, inputs, outputs, latency, cost = _usage(result)
                rows.append(
                    {
                        "case": case_index,
                        "step": index + 1,
                        "expected": fixture["expected"],
                        "selected": result.action.selected.action_id
                        if result.action.selected
                        else None,
                        "fallback": result.action.fallback_reason,
                        "calls": calls,
                        "input_tokens": inputs,
                        "output_tokens": outputs,
                        "latency_ms": latency,
                        "cost": cost,
                        "deferred": len(result.retention.deferred),
                        "action_probabilities": result.action.probabilities,
                        "capability_probabilities": result.tools.probabilities,
                        "retention_probabilities": result.retention.probabilities,
                    }
                )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row["fallback"] is None]
    correct = sum(row["selected"] == row["expected"] for row in valid)
    return {
        "top_1": {
            "correct": correct,
            "total": len(valid),
            "rate": correct / len(valid) if valid else None,
        },
        "fallbacks": len(rows) - len(valid),
        "jev_calls": sum(row["calls"] for row in rows),
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "latency_ms": sum(row["latency_ms"] for row in rows),
        "cost": str(sum(Decimal(row["cost"]) for row in rows)),
        "deferred_observations": sum(row["deferred"] for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    rows = run()
    report = {
        "benchmark": "live_integrated_controller_loop",
        "created_at": datetime.now(UTC).isoformat(),
        "summary": summarize(rows),
        "rows": rows,
        "limits": (
            "Fixed controller states measure retention, filtering, and routing. "
            "They do not execute a coding model or establish patch quality or "
            "whole-trajectory token savings."
        ),
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
