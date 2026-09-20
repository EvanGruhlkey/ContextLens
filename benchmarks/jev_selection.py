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


def measure_case(case: Case, state: Path, judge: Judge | None = None) -> dict[str, Any]:
    service = JevRepositoryContext(
        case.root, state, encoding="o200k_base", judge=judge
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
    required_found = sum(anchor in response for anchor in case.required)
    forbidden_found = sum(anchor in response for anchor in case.forbidden)
    response_tokens = service.count(response)
    full_tokens = service.count(full)
    passed = required_found == len(case.required) and forbidden_found == 0
    return {
        "case": case.case_id,
        "path": case.path,
        "task": case.task,
        "required_anchors": list(case.required),
        "required_anchors_found": required_found,
        "forbidden_anchors": list(case.forbidden),
        "forbidden_anchors_found": forbidden_found,
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
    }


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


def run(project: Path, judge: Judge | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="contextlens-jev-benchmark-") as temp:
        workspace = Path(temp)
        fixture = workspace / "fixture"
        _fixture(fixture)
        rows = [
            measure_case(case, workspace / f"state-{index}", judge)
            for index, case in enumerate(_cases(project, fixture))
        ]
    known_costs: list[Decimal] = []
    for row in rows:
        with suppress(InvalidOperation, TypeError):
            known_costs.append(Decimal(str(row["gateway_cost"])))
    return {
        "benchmark": "jev_primary_first_exact_evidence",
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
