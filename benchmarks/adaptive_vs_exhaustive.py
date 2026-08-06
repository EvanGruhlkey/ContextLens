"""Deterministic query-count benchmark for adaptive group ablation."""

from __future__ import annotations

import json
from dataclasses import asdict

from contextlens.benchmark import run_benchmark


def main() -> None:
    print(
        "Planner benchmark only. This does not measure end-to-end LLM task "
        "performance or production token savings."
    )
    print(json.dumps(asdict(run_benchmark()), indent=2))


if __name__ == "__main__":
    main()
