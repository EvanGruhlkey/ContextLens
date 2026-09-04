"""Run the released 0.6B pruner on realistic ContextLens source reads."""

from __future__ import annotations

import argparse
import ast
import json
import platform
import tempfile
import time
from pathlib import Path

import torch

from contextlens.pruning import (
    DEFAULT_SWE_PRUNER_MODEL,
    ContextPruner,
    LocalSwePrunerScorer,
    PruneRequest,
    ReceiptStore,
)

CASES = (
    (
        "repository-scope",
        "src/contextlens/repository.py",
        "Fix task-effective context resolution for a deleted target path",
        "Find how provider scope and deleted target paths are resolved",
    ),
    (
        "ast-repair",
        "src/contextlens/pruning/structure.py",
        "Preserve exception branches and imported definitions during pruning",
        "Trace control-flow and symbol dependency closure",
    ),
    (
        "paired-order",
        "src/contextlens/experiments/paired_runner.py",
        "Ensure paired agent trials alternate baseline and candidate order",
        "Find trial ordering and pair identity logic",
    ),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--model", default=DEFAULT_SWE_PRUNER_MODEL)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument(
        "--allow-cpu",
        action="store_true",
        help="accept the upstream runtime's slow 8,192-token CPU path",
    )
    arguments = parser.parse_args()
    if not torch.cuda.is_available() and not arguments.allow_cpu:
        parser.error("CUDA is unavailable; pass --allow-cpu to run anyway")

    root = arguments.repository.resolve()
    scorer = LocalSwePrunerScorer(arguments.model, allow_cpu=arguments.allow_cpu)
    device = "cpu"
    if torch.cuda.is_available():
        device = torch.cuda.get_device_name()
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="contextlens-benchmark-") as temporary:
        receipts = ReceiptStore(Path(temporary))
        pruner = ContextPruner(scorer, receipts)
        for case_id, relative_path, task, focus in CASES:
            source = (root / relative_path).read_text(encoding="utf-8")
            request = PruneRequest(
                task=task,
                focus=focus,
                content=source,
                tool="read_file",
                arguments={"path": relative_path},
                language="python",
                threshold=arguments.threshold,
            )
            started = time.perf_counter()
            result = pruner.prune(request)
            wall_ms = (time.perf_counter() - started) * 1000
            parse_valid = True
            try:
                ast.parse(result.text)
            except SyntaxError:
                parse_valid = False
            results.append(
                {
                    "case": case_id,
                    "path": relative_path,
                    "goal_hint": result.goal_hint,
                    "original_lines": result.original_lines,
                    "retained_lines": result.retained_lines,
                    "original_tokens": result.original_tokens,
                    "retained_tokens": result.retained_tokens,
                    "reduction_percent": round(result.reduction_fraction * 100, 1),
                    "wall_ms": round(wall_ms, 1),
                    "parse_valid": parse_valid,
                    "recovery_exact": receipts.read(result.receipt_id) == source,
                    "bypass_reason": result.bypass_reason,
                }
            )

    original = sum(int(item["original_tokens"]) for item in results)
    retained = sum(int(item["retained_tokens"]) for item in results)
    print(
        json.dumps(
            {
                "model": arguments.model,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "device": device,
                "cases": results,
                "summary": {
                    "cases": len(results),
                    "original_tokens": original,
                    "retained_tokens": retained,
                    "reduction_percent": round((1 - retained / original) * 100, 1),
                    "parse_valid": all(bool(item["parse_valid"]) for item in results),
                    "recovery_exact": all(
                        bool(item["recovery_exact"]) for item in results
                    ),
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
