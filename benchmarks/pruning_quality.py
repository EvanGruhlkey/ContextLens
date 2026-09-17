"""Deterministic structural audit, NOT a neural or agent-quality benchmark.

An oracle supplies one evidence line; expected support is independently specified.
This isolates whether structural repair loses known necessary context.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import tiktoken

from contextlens.pruning import (
    ContextPruner,
    PruneRequest,
    ReceiptStore,
    SemanticScores,
)


@dataclass(frozen=True)
class Case:
    name: str
    source: str
    evidence: str
    required: tuple[str, ...]


CASES = (
    Case(
        "lexical-shadowing",
        "def target():\n    value = 7\n    return value\n\n"
        "def unrelated():\n    value = 99\n    return value\n",
        "    return value",
        ("    value = 7",),
    ),
    Case(
        "branch-condition-dependency",
        "ENABLED = True\ndef target():\n    if ENABLED:\n        return 7\n",
        "        return 7",
        ("ENABLED = True", "    if ENABLED:"),
    ),
    Case(
        "multiline-function-header",
        "def target(\n    value: int,\n) -> int:\n    return value + 7\n",
        "    return value + 7",
        ("def target(", "    value: int,", ") -> int:"),
    ),
    Case(
        "multiline-control-header",
        "def target(value):\n    if (\n        value > 0\n    ):\n        return 7\n",
        "        return 7",
        ("    if (", "        value > 0", "    ):"),
    ),
    Case(
        "long-statement",
        "VALUES = [\n" + "".join(f"    {i},\n" for i in range(30)) + "]\n",
        "    15,",
        ("VALUES = [", "    0,", "    29,", "]"),
    ),
    Case(
        "transitive-dependency",
        "BASE = 7\nVALUE = BASE + 1\ndef target():\n    return VALUE\n",
        "    return VALUE",
        ("BASE = 7", "VALUE = BASE + 1"),
    ),
    Case(
        "parameter-shadows-global",
        "value = 99\ndef target(value):\n    return value + 7\n",
        "    return value + 7",
        ("def target(value):",),
    ),
)


class OracleScorer:
    backend_id = "oracle-evidence-not-a-model"

    def __init__(self, evidence_line: int) -> None:
        self.evidence_line = evidence_line

    def score(self, request: PruneRequest) -> SemanticScores:
        return SemanticScores(self.backend_id, {self.evidence_line: 1.0})


def run(encoding_name: str = "o200k_base") -> dict[str, object]:
    encoding = tiktoken.get_encoding(encoding_name)
    rows = []
    recovery = []
    # Explicit distractors make room for marker overhead. Savings on these
    # constructed cases must never be reported as production savings.
    noise = "\n" + "".join(f"unused_{i} = {i}\n" for i in range(80))
    with tempfile.TemporaryDirectory() as directory:
        store = ReceiptStore(Path(directory))
        for case in CASES:
            source = case.source + noise
            evidence = source.splitlines().index(case.evidence) + 1
            result = ContextPruner(OracleScorer(evidence), store).prune(
                PruneRequest(
                    task=f"Inspect {case.name}",
                    content=source,
                    minimum_tokens=0,
                    context_radius=0,
                )
            )
            ast.parse(result.text)
            missing = [
                line for line in case.required if line not in result.text.splitlines()
            ]
            rows.append(
                {
                    "case": case.name,
                    "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "required_support_present": not missing,
                    "missing_support": missing,
                    "bypass_reason": result.bypass_reason,
                    "original_tokens": len(
                        encoding.encode(source, disallowed_special=())
                    ),
                    "retained_tokens": len(
                        encoding.encode(result.text, disallowed_special=())
                    ),
                    "parse_valid": True,
                    "pruned": result.bypass_reason is None,
                    "output": result.text,
                }
            )
        for name, content in (
            ("lf", "a\nb\n"),
            ("crlf", "a\r\nb\r\n"),
            ("mixed", "a\r\nb\nc\r"),
        ):
            request = PruneRequest(task="Recover", content=content)
            receipt = store.save(request)
            exact = store.read(receipt.receipt_id) == content
            try:
                store.save(request)
                idempotent = True
            except RuntimeError:
                idempotent = False
            recovery.append({"case": name, "exact": exact, "idempotent": idempotent})
    return {
        "benchmark_kind": "synthetic_structural_audit",
        "agent_quality_measured": False,
        "neural_model_used": False,
        "production_savings_claim": None,
        "encoding": encoding_name,
        "python": platform.python_version(),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "working_tree_diff_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff", "--", "src"])
        ).hexdigest(),
        "cases": rows,
        "recovery": recovery,
        "summary": {
            "cases": len(rows),
            "support_passes": sum(row["required_support_present"] for row in rows),
            "pruned_cases": sum(row["pruned"] for row in rows),
            "exact_recovery_cases": sum(row["exact"] for row in recovery),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--encoding", default="o200k_base")
    args = parser.parse_args()
    report = run(args.encoding)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
