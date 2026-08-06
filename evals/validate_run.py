"""Validate a completed direct real-repository evaluation artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from contextlens.trace import TraceReader
from evals.run import FINAL_POLICIES


def validate_run(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    manifest = _load(run_dir / "manifest.json")
    _require(manifest.get("schema_version") == "2.0", "unsupported schema")
    _require(
        manifest.get("evaluation_kind") == "real_repository", "not a repository run"
    )
    _require(manifest.get("status") == "complete", "run is incomplete")
    _require(
        manifest.get("fresh_process_per_invocation") is True, "process reuse enabled"
    )
    _require(
        manifest.get("fresh_workspace_per_invocation") is True,
        "workspace reuse enabled",
    )
    _require(manifest.get("response_cache_enabled") is False, "response cache enabled")
    _require(
        tuple(manifest.get("comparison_groups", ())) == FINAL_POLICIES,
        "comparison groups must be full_context, contextlens, matched_random",
    )
    cases = _load(run_dir / "case-manifest.json")
    _require(isinstance(cases, list) and bool(cases), "case manifest is empty")
    for item in cases:
        _require(isinstance(item, dict), "invalid case manifest entry")
        case_id = str(item.get("case_id", ""))
        case_dir = run_dir / "cases" / case_id
        _require(
            (case_dir / "context-policy.json").is_file(), f"{case_id}: missing policy"
        )
        _require(
            (case_dir / "case-summary.txt").is_file(), f"{case_id}: missing summary"
        )
        traces = tuple((case_dir / "traces").glob("*.jsonl"))
        _require(len(traces) == 1, f"{case_id}: expected one baseline trace")
        reader = TraceReader(traces[0])
        reader.read_header()
        _require(bool(tuple(reader.events())), f"{case_id}: trace has no context")
        _require(bool(tuple(reader.steps())), f"{case_id}: trace has no steps")
    checksums = _load(run_dir / "checksums.json")
    _require(isinstance(checksums, dict), "checksums must be a mapping")
    for relative, expected in checksums.items():
        path = run_dir / str(relative)
        _require(path.is_file(), f"missing checksummed artifact: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        _require(actual == expected, f"checksum mismatch: {relative}")
    return {
        "status": "valid",
        "run_dir": str(run_dir),
        "evaluation_run_id": manifest.get("evaluation_run_id"),
        "suite": manifest.get("suite"),
        "cases": len(cases),
        "comparison_groups": list(FINAL_POLICIES),
        "invocations": manifest.get("attempted_invocation_count"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = validate_run(args.run_dir)
    except (OSError, ValueError) as error:
        print(f"INVALID EVALUATION: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


if __name__ == "__main__":
    raise SystemExit(main())
