"""Run the bounded six-case real-repository smoke suite."""

from __future__ import annotations

import argparse
from pathlib import Path

from evals.repository_cases import smoke_manifests
from evals.repository_runner import RepositoryRunOptions, run_repository_cases
from evals.run import DEFAULT_MODEL, DEFAULT_REASONING


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("smoke",), required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--reasoning", default=DEFAULT_REASONING)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-experiments", type=int, default=2)
    parser.add_argument("--output-root", type=Path, default=Path("evals/artifacts"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifests = smoke_manifests()
    if len(manifests) != 6:
        raise SystemExit(
            f"smoke suite must contain exactly six cases, found {len(manifests)}"
        )
    run_dir = run_repository_cases(
        manifests,
        suite=args.suite,
        options=RepositoryRunOptions(
            model=args.model,
            reasoning=args.reasoning,
            timeout_seconds=args.timeout_seconds,
            max_experiments=args.max_experiments,
            trials=args.trials,
            output_root=args.output_root,
        ),
    )
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
