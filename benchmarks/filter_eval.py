"""Paired observation-filter evaluation with three identical conditions."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from contextlens.filtering import FilterConfig, FilterRequest, ObservationFilter
from contextlens.jev_gateway import Evaluation
from contextlens.pruning.model import ObservationKind, estimate_tokens
from contextlens.pruning.receipts import ReceiptStore

SOURCE = """TIMEOUT = 30
UNRELATED = "noise"

def refresh_token():
    return TIMEOUT

def create_session():
    return {"ok": True}

def validate_refresh_token(token):
    return token is not None

def logout():
    return "bye"
""" + "".join(
    f"\ndef unused_helper_{index}():\n    return UNRELATED\n" for index in range(40)
)

SEARCH = """auth.py:4:def refresh_token():
auth.py:8:def create_session():
auth.py:16:def logout():
theme.py:2:def refresh_theme():
""" + "\n".join(f"noise.py:{index}:unrelated hit {index}" for index in range(20))

TEST_OUTPUT = """
============================= test session starts ==============================
collected 12 items
""" + "\n".join(
    f"test_auth.py::test_unrelated_{index} PASSED" for index in range(10)
) + """
test_auth.py::test_refresh FAILED
test_auth.py::test_logout PASSED
=========================== FAILURES ===========================================
______________________________ test_refresh _________________________________
AssertionError: assert 900 == 3600
test_auth.py:12: AssertionError
=========================== short test summary info ============================
FAILED test_auth.py::test_refresh - AssertionError
""" + "\n".join(
    f"PASSED test_auth.py::test_unrelated_{index}" for index in range(10)
)

TASK = "fix the refresh-token timeout"


class FixtureJudge:
    def evaluate(self, state: dict[str, Any], questions: dict[str, Any]) -> Evaluation:
        probabilities = {}
        for key, candidate in state["candidates"].items():
            blob = json.dumps(candidate)
            keep = any(
                token in blob
                for token in ("refresh_token", "TIMEOUT", "AssertionError", "900")
            )
            probabilities[key] = 0.9 if keep else 0.1
        return Evaluation(probabilities, "fixture", 20, 4, "0", 1.0)


def _observations() -> list[FilterRequest]:
    return [
        FilterRequest(
            TASK,
            SOURCE,
            tool="read_file",
            arguments={"path": "auth.py"},
            kind=ObservationKind.CODE,
            path="auth.py",
        ),
        FilterRequest(TASK, SEARCH, tool="rg", kind=ObservationKind.SEARCH),
        FilterRequest(TASK, TEST_OUTPUT, tool="pytest", kind=ObservationKind.TEST),
    ]


def run_condition(name: str, receipts: Path, **config: Any) -> dict[str, Any]:
    started = time.perf_counter()
    pipeline = ObservationFilter(
        ReceiptStore(receipts),
        judge=None if name == "baseline" else FixtureJudge(),
        config=FilterConfig(minimum_tokens=0, **config),
    )
    rows = []
    for request in _observations():
        if name == "baseline":
            tokens = estimate_tokens(request.content)
            rows.append(
                {
                    "kind": (request.kind or ObservationKind.TEXT).value,
                    "raw_tokens": tokens,
                    "injected_tokens": tokens,
                    "jev_input_tokens": 0,
                    "jev_output_tokens": 0,
                    "recovery_calls": 0,
                    "bypass_reason": "baseline",
                }
            )
            continue
        result = pipeline.filter(request)
        recovered = 0
        if result.omitted_ranges:
            pipeline.receipts.read(result.receipt_id)
            recovered = 1
        rows.append(
            {
                "kind": result.kind.value,
                "raw_tokens": result.original_tokens,
                "injected_tokens": result.retained_tokens,
                "jev_input_tokens": result.jev_input_tokens,
                "jev_output_tokens": result.jev_output_tokens,
                "recovery_calls": recovered,
                "bypass_reason": result.bypass_reason,
            }
        )
    report = {
        "condition": name,
        "task_success": None,
        "coding_model_input_tokens": None,
        "coding_model_uncached_input_tokens": None,
        "coding_model_output_tokens": None,
        "agent_turns": None,
        "raw_tool_output_tokens": sum(row["raw_tokens"] for row in rows),
        "injected_tool_output_tokens": sum(row["injected_tokens"] for row in rows),
        "recovery_calls": sum(row["recovery_calls"] for row in rows),
        "jev_input_tokens": sum(row["jev_input_tokens"] for row in rows),
        "jev_output_tokens": sum(row["jev_output_tokens"] for row in rows),
        "jev_cost": "0" if name != "baseline" else None,
        "latency_ms": (time.perf_counter() - started) * 1000,
        "observations": rows,
    }
    return report


def analyze(reports: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {item["condition"]: item for item in reports}
    baseline = by_name["baseline"]["injected_tool_output_tokens"]
    full = by_name["contextlens"]["injected_tool_output_tokens"]
    reduction = None
    if baseline:
        reduction = round(100 * (baseline - full) / baseline, 2)
    return {
        "primary_metric": "injected_tool_output_tokens",
        "coding_model_metrics_measured": False,
        "injected_token_reduction_percent": reduction,
        "success_gate": {
            "task_success_does_not_regress": None,
            "coding_model_input_decreases": None,
            "agent_turns_do_not_increase": None,
            "injected_tool_output_decreases": bool(
                reduction is not None and reduction > 0
            ),
        },
        "conditions": by_name,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Paired baseline / Jev-filter / full ContextLens evaluation"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/results/filter-eval.json"),
    )
    arguments = parser.parse_args(argv)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reports = [
            run_condition("baseline", root / "baseline"),
            run_condition(
                "jev_observations", root / "jev", expand_structure=False
            ),
            run_condition("contextlens", root / "full", expand_structure=True),
        ]
    payload = analyze(reports)
    arguments.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
