"""Real-model observation benchmark; does not measure downstream agent quality."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from contextlens.pruning import (
    DEFAULT_SWE_PRUNER_MODEL,
    ContextPruner,
    HttpSemanticScorer,
    LocalSwePrunerScorer,
    PruneRequest,
    ReceiptStore,
    SemanticScorer,
    estimate_tokens,
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


def _write(report: dict, output: Path | None) -> None:
    body = json.dumps(report, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(body, encoding="utf-8")
    else:
        print(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--model", default=DEFAULT_SWE_PRUNER_MODEL)
    parser.add_argument("--backend", choices=("local", "http"), default="local")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000/prune")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--case", choices=[case[0] for case in CASES])
    parser.add_argument("--encoding", default="o200k_base")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-cpu", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1 or not 0 <= args.threshold <= 1 or args.timeout <= 0:
        parser.error(
            "repeats and timeout must be positive; threshold must be in [0, 1]"
        )
    root = args.repository.resolve()
    cases = tuple(case for case in CASES if args.case is None or case[0] == args.case)
    report: dict = {
        "benchmark_kind": "real_model_observation_microbenchmark",
        "status": "initializing",
        "agent_quality_measured": False,
        "provider_usage_measured": False,
        "production_savings_claim": None,
        "model_requested": args.model if args.backend == "local" else None,
        "model_revision_verified": False,
        "backend": args.backend,
        "encoding": args.encoding,
        "threshold": args.threshold,
        "repeats": args.repeats,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "commit": subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
        ).strip(),
        "working_tree_diff_sha256": hashlib.sha256(
            subprocess.check_output(
                ["git", "-C", str(root), "diff", "--", "src"],
            )
        ).hexdigest(),
        "packages": {},
        "cases": [],
    }
    for package in ("torch", "swe-pruner", "transformers", "tiktoken"):
        try:
            report["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report["packages"][package] = None
    scorer: SemanticScorer
    try:
        if args.backend == "local":
            import torch

            report["cuda_available"] = torch.cuda.is_available()
            report["device"] = (
                torch.cuda.get_device_name() if torch.cuda.is_available() else "cpu"
            )
            scorer = LocalSwePrunerScorer(args.model, allow_cpu=args.allow_cpu)
        else:
            scorer = HttpSemanticScorer(args.backend_url, timeout_seconds=args.timeout)
        import tiktoken

        encoding = tiktoken.get_encoding(args.encoding)
    except (ImportError, RuntimeError, ValueError) as error:
        report.update(status="blocked", error=str(error))
        _write(report, args.output)
        return 2

    def count(text: str) -> int:
        return len(encoding.encode(text, disallowed_special=()))

    report["status"] = "running"
    _write(report, args.output)
    with tempfile.TemporaryDirectory(prefix="contextlens-benchmark-") as directory:
        receipts = ReceiptStore(Path(directory))
        pruner = ContextPruner(scorer, receipts, token_counter=count)
        for repeat in range(args.repeats):
            order = cases[repeat % len(cases) :] + cases[: repeat % len(cases)]
            for case_id, relative_path, task, focus in order:
                source = (root / relative_path).read_bytes().decode("utf-8")
                request = PruneRequest(
                    task=task,
                    focus=focus,
                    content=source,
                    tool="read_file",
                    arguments={"path": relative_path},
                    language="python",
                    threshold=args.threshold,
                )
                started = time.perf_counter()
                result = pruner.prune(request)
                wall_ms = (time.perf_counter() - started) * 1000
                try:
                    ast.parse(result.text)
                    parse_valid = True
                except SyntaxError:
                    parse_valid = False
                report["cases"].append(
                    {
                        "case": case_id,
                        "repeat": repeat,
                        "path": relative_path,
                        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                        "goal_hint": result.goal_hint,
                        "first_request": not report["cases"],
                        "original_tokens": count(source),
                        "retained_tokens": count(result.text),
                        "estimated_original_tokens": estimate_tokens(source),
                        "estimated_retained_tokens": estimate_tokens(result.text),
                        "wall_ms": wall_ms,
                        "parse_valid": parse_valid,
                        "recovery_exact": receipts.read(result.receipt_id) == source,
                        "bypass_reason": result.bypass_reason,
                        "actual_backend": result.backend,
                        "output": result.text,
                    }
                )
                _write(report, args.output)

    rows = report["cases"]
    original = sum(row["original_tokens"] for row in rows)
    retained = sum(row["retained_tokens"] for row in rows)
    warm = [row["wall_ms"] for row in rows if not row["first_request"]]
    failures = [
        row
        for row in rows
        if row["bypass_reason"] == "semantic_backend_error"
        or not row["parse_valid"]
        or not row["recovery_exact"]
    ]
    report["status"] = "invalid" if failures else "complete"
    report["summary"] = {
        "observations": len(rows),
        "distinct_tasks": len(cases),
        "failed_observations": len(failures),
        "pruned_observations": sum(row["bypass_reason"] is None for row in rows),
        "original_tokens": original,
        "retained_tokens": retained,
        "observation_reduction_fraction": 1 - retained / original if original else 0,
        "first_request_wall_ms": rows[0]["wall_ms"],
        "subsequent_request_median_ms": statistics.median(warm) if warm else None,
        "note": "Repeated reads measure latency, not independent quality trials. "
        "Token counts cover returned text including markers, not total agent usage.",
    }
    _write(report, args.output)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
