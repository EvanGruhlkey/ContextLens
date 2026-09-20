"""Live Jev exact-evidence benchmark; measures selection, not patch quality."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from contextlens.jev_context import JevRepositoryContext, Judge


@dataclass(frozen=True)
class Case:
    case_id: str
    root: Path
    path: str
    task: str
    focus: str
    required: tuple[str, ...]
    forbidden: tuple[str, ...] = ()


def measure_case(
    case: Case,
    state: Path,
    judge: Judge | None = None,
    *,
    selection_strategy: str = "full_source",
) -> dict[str, Any]:
    service = JevRepositoryContext(
        case.root,
        state,
        encoding="o200k_base",
        judge=judge,
        selection_strategy=selection_strategy,
    )
    full = service.call("read", {"path": case.path, "budget": 16000})
    if full.startswith("Evidence "):
        raise RuntimeError(f"full-file baseline exceeds budget: {case.path}")
    response = service.call(
        "select",
        {
            "task": case.task,
            "focus": case.focus,
            "budget": 3000,
            "limit": 12,
        },
    )
    audit = json.loads((state / "selections.jsonl").read_text().splitlines()[-1])
    evaluation = audit["evaluation"]
    descriptor = audit.get("descriptor_evaluation") or {}
    required_found = sum(anchor in response for anchor in case.required)
    forbidden_found = sum(anchor in response for anchor in case.forbidden)
    missing_required = [anchor for anchor in case.required if anchor not in response]
    present_forbidden = [anchor for anchor in case.forbidden if anchor in response]
    response_tokens = service.count(response)
    full_tokens = service.count(full)
    passed = required_found == len(case.required) and forbidden_found == 0
    return {
        "case": case.case_id,
        "selection_strategy": selection_strategy,
        "path": case.path,
        "task": case.task,
        "required_anchors": list(case.required),
        "required_anchors_found": required_found,
        "missing_required_anchors": missing_required,
        "forbidden_anchors": list(case.forbidden),
        "forbidden_anchors_found": forbidden_found,
        "present_forbidden_anchors": present_forbidden,
        "evidence_check": "passed" if passed else "failed",
        "full_read_tokens": full_tokens,
        "selection_response_tokens": response_tokens,
        "returned_text_reduction_percent": round(
            (1 - response_tokens / full_tokens) * 100, 1
        ),
        "selected_candidates": len(audit["selected"]),
        "deferred_candidates": len(audit["deferred"]),
        "gateway_model": evaluation["model"],
        "gateway_input_tokens": evaluation["input_tokens"],
        "gateway_output_tokens": evaluation["output_tokens"],
        "gateway_cost": evaluation["cost"],
        "gateway_latency_ms": evaluation["latency_ms"],
        "descriptor_input_tokens": descriptor.get("input_tokens"),
        "descriptor_output_tokens": descriptor.get("output_tokens"),
        "descriptor_cost": descriptor.get("cost"),
        "descriptor_latency_ms": descriptor.get("latency_ms"),
        "decision_input_tokens": _sum_usage(
            descriptor.get("input_tokens"), evaluation["input_tokens"]
        ),
        "decision_output_tokens": _sum_usage(
            descriptor.get("output_tokens"), evaluation["output_tokens"]
        ),
        "decision_cost": _sum_cost(descriptor.get("cost"), evaluation["cost"]),
        "decision_latency_ms": round(
            float(descriptor.get("latency_ms") or 0) + evaluation["latency_ms"],
            3,
        ),
    }


def _sum_usage(first: int | None, second: int | None) -> int | None:
    if second is None:
        return None
    return (first or 0) + second


def _sum_cost(first: object, second: object) -> str | None:
    values = [value for value in (first, second) if value is not None]
    if not values:
        return None
    try:
        return str(sum(Decimal(str(value)) for value in values))
    except InvalidOperation:
        return None


def _sum_costs(values: Any) -> str | None:
    costs: list[Decimal] = []
    for value in values:
        if value is None:
            continue
        with suppress(InvalidOperation, TypeError):
            costs.append(Decimal(str(value)))
    return str(sum(costs)) if costs else None


def _fixture(root: Path) -> None:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    noise = "\n".join(f"UNRELATED_{i} = 'noise {i}'" for i in range(300))
    (root / "auth.py").write_text(
        "TIMEOUT = 30\n\ndef refresh_token():\n    return TIMEOUT\n" + noise,
        encoding="utf-8",
    )
    (root / "service.py").write_text(
        "class Client:\n    TIMEOUT = 20\n"
        "    def refresh_token(self):\n        return self.TIMEOUT\n"
        + "".join(
            f"    def unrelated_{number}(self):\n        return {number}\n"
            for number in range(100)
        ),
        encoding="utf-8",
    )
    (root / "helpers.py").write_text(
        "LIMIT = 30\n\ndef helper():\n    return LIMIT\n"
        "\ndef refresh_token():\n    return helper()\n"
        + noise,
        encoding="utf-8",
    )


def _cases(project: Path, fixture: Path) -> list[Case]:
    return [
        Case(
            "fixture-function-support",
            fixture,
            "auth.py",
            "find the refresh token timeout implementation and its value",
            "refresh_token timeout",
            ("def refresh_token", "TIMEOUT = 30"),
            ("UNRELATED_",),
        ),
        Case(
            "fixture-method-support",
            fixture,
            "service.py",
            "find the client refresh token timeout behavior",
            "Client.refresh_token",
            ("class Client:", "def refresh_token", "TIMEOUT = 20"),
            ("def unrelated_",),
        ),
        Case(
            "fixture-transitive-support",
            fixture,
            "helpers.py",
            "find refresh_token and the helper values that determine its result",
            "refresh_token helper LIMIT",
            ("def refresh_token", "def helper", "LIMIT = 30"),
            ("UNRELATED_",),
        ),
        Case(
            "repository-gateway-validation",
            project,
            "src/contextlens/jev_gateway.py",
            "find the code that validates Jev gateway evaluation responses",
            "gateway response validation",
            ("def parse_evaluation", "invalid probability"),
        ),
        Case(
            "repository-scope-analysis",
            project,
            "src/contextlens/context_index.py",
            "find how ContextLens distinguishes global and nonlocal declarations",
            "_scope_declarations",
            ("def _scope_declarations", "ast.Global", "ast.Nonlocal"),
        ),
    ]


def run(
    project: Path,
    judge: Judge | None = None,
    *,
    selection_strategy: str = "full_source",
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="contextlens-jev-benchmark-") as temp:
        workspace = Path(temp)
        fixture = workspace / "fixture"
        _fixture(fixture)
        rows = [
            measure_case(
                case,
                workspace / f"state-{index}",
                judge,
                selection_strategy=selection_strategy,
            )
            for index, case in enumerate(_cases(project, fixture))
        ]
    known_costs: list[Decimal] = []
    for row in rows:
        with suppress(InvalidOperation, TypeError):
            known_costs.append(Decimal(str(row["gateway_cost"])))
    return {
        "benchmark": "jev_primary_first_exact_evidence",
        "selection_strategy": selection_strategy,
        "repository_revision": subprocess.check_output(
            ["git", "-C", str(project), "rev-parse", "HEAD"], text=True
        ).strip(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "encoding": "o200k_base",
        "limits": (
            "Component benchmark with fixed development cases; measures evidence "
            "anchors and context costs, not coding-agent patch quality."
        ),
        "summary": {
            "cases": len(rows),
            "evidence_passes": sum(
                row["evidence_check"] == "passed" for row in rows
            ),
            "full_read_tokens": sum(row["full_read_tokens"] for row in rows),
            "selection_response_tokens": sum(
                row["selection_response_tokens"] for row in rows
            ),
            "gateway_input_tokens": sum(
                row["gateway_input_tokens"] or 0 for row in rows
            ),
            "gateway_output_tokens": sum(
                row["gateway_output_tokens"] or 0 for row in rows
            ),
            "gateway_cost": str(sum(known_costs)) if known_costs else None,
            "decision_input_tokens": sum(
                row["decision_input_tokens"] or 0 for row in rows
            ),
            "decision_output_tokens": sum(
                row["decision_output_tokens"] or 0 for row in rows
            ),
            "decision_cost": _sum_costs(row["decision_cost"] for row in rows),
        },
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/results/jev-selection.json"),
    )
    args = parser.parse_args()
    report = run(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    passed = report["summary"]["evidence_passes"] == report["summary"]["cases"]
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
