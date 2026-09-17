"""CPU retrieval ablation; source/JSON counts do not measure agent quality."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from contextlens.evidence import retrieve_evidence
from contextlens.evidence_index import build_index
from contextlens.evidence_session import tokenizer
from contextlens.pruning import ReceiptStore
from evals.repository_cases import acquire_repository, load_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    count, method = tokenizer("o200k_base")
    rows = []
    for case in args.case:
        manifest = load_manifest(case)
        root = acquire_repository(manifest, output / manifest.case_id)
        for policy in ("full", "lexical", "dependency"):
            state = output / (manifest.case_id + "-" + policy)
            for repeat in range(args.repeats):
                started = time.perf_counter()
                index = build_index(root, state / "index.sqlite")
                bundle = retrieve_evidence(
                    root,
                    manifest.task,
                    ReceiptStore(state / "receipts"),
                    index=index,
                    policy=policy,
                    budget=30000 if policy == "full" else 3000,
                    token_counter=count,
                    token_count_method=method,
                )
                elapsed = time.perf_counter() - started
                (state / f"bundle-{repeat}.json").write_text(
                    json.dumps(bundle, indent=2), encoding="utf-8"
                )
                rows.append(
                    {
                        "case": manifest.case_id,
                        "commit": manifest.commit,
                        "policy": policy,
                        "repeat": repeat,
                        "cache_hits": index.cache_hits,
                        "indexed_files": len(index.sources),
                        "source_tokens": bundle["source_tokens"],
                        "serialized_response_tokens": bundle["response_tokens"],
                        "selected_spans": len(bundle["spans"]),
                        "unresolved_dependencies": bundle[
                            "unresolved_dependency_count"
                        ],
                        "wall_seconds": elapsed,
                    }
                )
    report = {
        "benchmark_kind": "cpu_retrieval_ablation",
        "token_encoding": method,
        "quality_claim": None,
        "provider_usage_claim": None,
        "rows": rows,
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"runs": len(rows), "report": str(output / "report.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
