"""Paired agent evaluation of gross input tokens and mechanically verified fixes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.comprehensive import source_hash
from benchmarks.evidence_agent import dump, isolated_agent_environment, verify
from contextlens.experiments.codex_cli import _parse_jsonl
from evals.repository_cases import (
    RepositoryCaseManifest,
    acquire_repository,
    load_manifest,
)

POLICIES = ("normal", "compact")


def usage(events: list[dict[str, Any]], complete: bool) -> dict[str, int | None]:
    """Sum terminal turn aggregates; cached input is already part of input."""
    turns = [e.get("usage", {}) for e in events if e.get("type") == "turn.completed"]
    result: dict[str, int | None] = {}
    for field in ("input_tokens", "cached_input_tokens", "output_tokens"):
        values = [turn.get(field) for turn in turns]
        known = (
            complete
            and bool(values)
            and all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in values
            )
        )
        result[field] = sum(values) if known else None
    gross = result["input_tokens"]
    cached = result["cached_input_tokens"]
    output = result["output_tokens"]
    if gross is not None and cached is not None and cached > gross:
        raise ValueError("cached input exceeds gross input")
    result["uncached_input_tokens"] = (
        gross - cached if gross is not None and cached is not None else None
    )
    result["total_tokens"] = (
        gross + output if gross is not None and output is not None else None
    )
    return result


def command_for(
    workspace: Path, state: Path, policy: str, model: str, codex: str
) -> list[str]:
    command = [
        codex,
        "exec",
        "--ephemeral",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
        "--disable",
        "plugins",
        "--enable",
        "skip_host_skill_discovery",
        "--sandbox",
        "workspace-write",
        "--model",
        model,
        "-c",
        'model_reasoning_effort="low"',
        "-c",
        "project_doc_max_bytes=0",
    ]
    if policy == "compact":
        command += [
            "-c",
            "mcp_servers.contextlens.command=" + json.dumps(sys.executable),
            "-c",
            "mcp_servers.contextlens.args="
            + json.dumps(
                [
                    "-m",
                    "contextlens.pruning_cli",
                    "mcp",
                    "--profile",
                    "compact",
                    "--root",
                    str(workspace),
                    "--state",
                    str(state),
                    "--encoding",
                    "o200k_base",
                ]
            ),
            "-c",
            "mcp_servers.contextlens.required=true",
            "-c",
            'mcp_servers.contextlens.default_tools_approval_mode="approve"',
        ]
    elif policy != "normal":
        raise ValueError("unknown benchmark policy")
    if os.name == "nt":
        command += ["-c", 'windows.sandbox="elevated"']
    return command + ["-"]


def prompt(task: str, policy: str) -> str:
    text = (
        "Resolve this repository task with a minimal patch. Search, read, edit and "
        "test with the available tools. Do not inspect parent directories, access "
        "the network or inspect Git history. Do not commit or push.\n\nTask:\n" + task
    )
    if policy == "compact":
        text += (
            "\n\nUse the ContextLens MCP context_find/context_read tools for "
            "repository discovery and source reads. Discover these tools through "
            "tool search if needed. Read known paths directly; find only when "
            "needed. Use normal tools for edits and tests, and unsupported reads. "
            "If ContextLens cannot provide needed context, recover or fall back "
            "and explain why. Do not skip ContextLens entirely."
        )
    return text


def patch(workspace: Path) -> bytes:
    # Include newly created tests/source, not just tracked modifications.
    subprocess.run(
        ["git", "add", "-N", "."], cwd=workspace, check=True, capture_output=True
    )
    return subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=workspace)


def grade(
    base: Path, destination: Path, diff: bytes, manifest: RepositoryCaseManifest
) -> dict[str, Any]:
    """Replay the patch on a fresh checkout with external, frozen assertions."""
    shutil.copytree(
        base, destination, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
    )
    if diff:
        applied = subprocess.run(
            ["git", "apply", "--binary", "-"],
            input=diff,
            cwd=destination,
            capture_output=True,
            check=False,
        )
        if applied.returncode:
            return {
                "success": False,
                "commands": [],
                "patch_apply_error": applied.stderr.decode(errors="replace"),
            }
    return verify(destination, manifest.verification)


def attempt(
    manifest: RepositoryCaseManifest,
    base: Path,
    output: Path,
    trial: int,
    policy: str,
    model: str,
    codex: str,
    timeout: int,
) -> dict[str, Any]:
    run_dir = output / f"{manifest.case_id}-{trial}-{policy}"
    run_dir.mkdir()
    workspace = run_dir / "workspace"
    shutil.copytree(base, workspace, ignore=shutil.ignore_patterns("__pycache__"))
    environment = isolated_agent_environment()
    environment["PYTHONPATH"] = str(output / "snapshot" / "src")
    environment["PATH"] = (
        str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
    )
    text = prompt(manifest.task, policy)
    (run_dir / "prompt.txt").write_text(text, encoding="utf-8")
    started = time.perf_counter()
    returncode = None
    try:
        completed = subprocess.run(
            command_for(workspace, run_dir / "state", policy, model, codex),
            cwd=workspace,
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
            check=False,
        )
        raw, stderr, returncode = (
            completed.stdout,
            completed.stderr,
            completed.returncode,
        )
        status = "completed" if returncode == 0 else "agent_process_error"
    except subprocess.TimeoutExpired as error:
        raw = error.stdout or ""
        raw = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
        stderr, status = str(error), "timeout"
    agent_seconds = time.perf_counter() - started
    (run_dir / "agent.jsonl").write_text(raw, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    parsed = _parse_jsonl(raw)
    if any(
        marker in (raw + stderr).lower()
        for marker in ("usage_limit_reached", "you've hit your usage limit")
    ):
        status = "subscription_limit_reached"
    if parsed.parse_errors:
        status = "invalid_event_stream"
    terminal = any(event.get("type") == "turn.completed" for event in parsed.events)
    if status == "completed" and not terminal:
        status = "missing_terminal_usage"
    current = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=workspace, text=True
    ).strip()
    if current != manifest.commit:
        status = "invalid_repository_revision"
    diff = patch(workspace)
    (run_dir / "patch.diff").write_bytes(diff)
    verify_started = time.perf_counter()
    verification = grade(base, run_dir / "grading", diff, manifest)
    verification_seconds = time.perf_counter() - verify_started
    dump(run_dir / "verification.json", verification)
    if any(command["returncode"] is None for command in verification["commands"]):
        status = "verification_infrastructure_error"
    calls_path = run_dir / "state" / "calls.jsonl"
    calls = (
        [
            json.loads(line)
            for line in calls_path.read_text(encoding="utf-8").splitlines()
        ]
        if calls_path.exists()
        else []
    )
    exact_reads = sum(
        call["operation"] == "read"
        and call["response"].startswith("Exact current source;")
        and "\n" in call["response"]
        for call in calls
    )
    if policy == "compact" and status == "completed" and not exact_reads:
        status = "invalid_contextlens_not_used"
    row = {
        "case": manifest.case_id,
        "trial": trial,
        "policy": policy,
        "status": status,
        "verified_success": status == "completed" and verification["success"],
        "patch_checks_passed": verification["success"],
        "verification": verification,
        "returncode": returncode,
        **usage(
            parsed.events, returncode == 0 and terminal and not parsed.parse_errors
        ),
        "agent_seconds": agent_seconds,
        "verification_seconds": verification_seconds,
        "contextlens_calls": len(calls),
        "contextlens_read_calls": exact_reads,
        "contextlens_returned_tokens": sum(call["response_tokens"] for call in calls),
        "shell_calls": len(parsed.command_events),
        "patch_sha256": hashlib.sha256(diff).hexdigest(),
        "agent_errors": parsed.errors,
        "parse_errors": parsed.parse_errors,
    }
    dump(run_dir / "row.json", row)
    return row


def analyze(rows: list[dict[str, Any]], expected_pairs: int) -> dict[str, Any]:
    conditions = {}
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
        "total_tokens",
    )
    for policy in POLICIES:
        selected = [row for row in rows if row["policy"] == policy]
        conditions[policy] = {
            "attempts": len(selected),
            "passed": sum(row["verified_success"] for row in selected),
            **{
                field: sum(row[field] for row in selected)
                if selected and all(row.get(field) is not None for row in selected)
                else None
                for field in fields
            },
            "median_agent_seconds": statistics.median(
                row["agent_seconds"] for row in selected
            )
            if selected
            else None,
            "contextlens_calls": sum(row["contextlens_calls"] for row in selected),
            "runs_with_contextlens_reads": sum(
                row["contextlens_read_calls"] > 0 for row in selected
            ),
            "incomplete_or_invalid": sum(
                row["status"] != "completed" for row in selected
            ),
        }
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in rows:
        key = (row["case"], row["trial"])
        group = grouped.setdefault(key, {})
        if row["policy"] in group:
            raise ValueError("duplicate attempt")
        group[row["policy"]] = row
    pairs = [
        group
        for group in grouped.values()
        if all(
            policy in group
            and group[policy]["status"] == "completed"
            and group[policy].get("input_tokens") is not None
            for policy in POLICIES
        )
    ]
    first = sum(pair["normal"]["input_tokens"] for pair in pairs)
    second = sum(pair["compact"]["input_tokens"] for pair in pairs)
    regressions = [
        pair["normal"]["case"]
        for pair in pairs
        if pair["normal"]["verified_success"]
        and not pair["compact"]["verified_success"]
    ]
    complete = len(pairs) == expected_pairs and len(rows) == expected_pairs * 2
    uptake = all(pair["compact"]["contextlens_read_calls"] > 0 for pair in pairs)
    return {
        "conditions": conditions,
        "complete_pairs": len(pairs),
        "expected_pairs": expected_pairs,
        "paired_gross_input_reduction_percent": round((1 - second / first) * 100, 2)
        if first
        else None,
        "observed_quality_regressions": regressions,
        "all_candidate_runs_used_contextlens_reads": bool(pairs) and uptake,
        "observed_sample_meets_goal": second < first
        and all(pair["compact"]["verified_success"] for pair in pairs)
        and not regressions
        if complete and uptake
        else None,
        "statistical_quality_preservation_claim": None,
        "dollar_cost_usd": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--codex", default="codex")
    args = parser.parse_args()
    if min(args.trials, args.timeout) < 1:
        parser.error("trials and timeout must be positive")
    project = Path(__file__).resolve().parents[1]
    fixtures = project / "benchmarks" / "fixtures" / "goal"
    manifests = [load_manifest(path) for path in sorted(fixtures.glob("*.json"))]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(
        project / "src",
        output / "snapshot" / "src",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    report: dict[str, Any] = {
        "benchmark": "paired_compact_mcp_agent_pilot",
        "started_at": datetime.now(UTC).isoformat(),
        "implementation_sha256": source_hash(project),
        "implementation_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip(),
        "model": args.model,
        "reasoning": "low",
        "trials": args.trials,
        "order_seed": 731,
        "integration": "compact MCP; native tools available; callback adapter not used",
        "usage_definition": (
            "input includes cached input; uncached=input-cached; total=input+output"
        ),
        "api_spend_initiated": False,
        "manifests": [manifest.public_value() for manifest in manifests],
        "preflights": [],
        "rows": [],
        "status": "preflight",
    }
    dump(output / "report.json", report)
    (output / "driver-source.py").write_bytes(Path(__file__).read_bytes())
    bases = {}
    for manifest in manifests:
        base = acquire_repository(manifest, output / (manifest.case_id + "-base"))
        before = verify(base, manifest.verification)
        golden = fixtures / (manifest.case_id + ".patch")
        after = grade(
            base,
            output / (manifest.case_id + "-calibration"),
            golden.read_bytes(),
            manifest,
        )
        report["preflights"].append(
            {
                "case": manifest.case_id,
                "before": before,
                "known_fix": after,
                "known_fix_sha256": hashlib.sha256(golden.read_bytes()).hexdigest(),
            }
        )
        dump(output / "report.json", report)
        if (
            before["success"]
            or not after["success"]
            or any(command["returncode"] is None for command in before["commands"])
        ):
            raise RuntimeError(f"invalid verification calibration: {manifest.case_id}")
        bases[manifest.case_id] = base
    rng = random.Random(731)
    report["status"] = "running"
    dump(output / "report.json", report)
    for manifest in manifests:
        for trial in range(args.trials):
            policies = list(POLICIES)
            rng.shuffle(policies)
            for policy in policies:
                row = attempt(
                    manifest,
                    bases[manifest.case_id],
                    output,
                    trial,
                    policy,
                    args.model,
                    args.codex,
                    args.timeout,
                )
                report["rows"].append(row)
                report["analysis"] = analyze(
                    report["rows"], len(manifests) * args.trials
                )
                dump(output / "report.json", report)
                print(
                    json.dumps(
                        {
                            key: row[key]
                            for key in (
                                "case",
                                "policy",
                                "status",
                                "verified_success",
                                "input_tokens",
                            )
                        }
                    ),
                    flush=True,
                )
                if row["status"] == "subscription_limit_reached":
                    report["status"] = "subscription_limit_reached"
                    dump(output / "report.json", report)
                    return 1
    report["status"] = (
        "completed"
        if all(row["status"] == "completed" for row in report["rows"])
        else "completed_with_invalid_attempts"
    )
    report["completed_at"] = datetime.now(UTC).isoformat()
    dump(output / "report.json", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
