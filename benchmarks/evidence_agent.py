"""Paired real-repository agent pilot with live ContextLens MCP tools.

Full-file retrieval and dependency-aware spans share the same discovery index
and tool schemas. Baseline gets a larger source budget to permit whole files.
This is a pilot, not proof of non-inferiority or universal token savings.
"""

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
from pathlib import Path
from typing import Any

from benchmarks.evidence_analysis import analyze_pairs
from contextlens.evidence import retrieve_evidence
from contextlens.evidence_index import build_index
from contextlens.evidence_session import tokenizer
from contextlens.experiments.codex_cli import _parse_jsonl
from contextlens.pruning import ReceiptStore
from evals.repository_cases import acquire_repository, load_manifest


def dump(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def isolated_agent_environment() -> dict[str, str]:
    """Keep authentication but separate child runs from desktop session routing."""
    return {
        name: value
        for name, value in os.environ.items()
        if (not name.startswith("CODEX_") or name == "CODEX_HOME")
        and name != "OPENAI_API_KEY"
    }


def verify(workspace: Path, commands: tuple[tuple[str, ...], ...]) -> dict[str, Any]:
    results = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
            results.append(
                {
                    "command": list(command),
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            results.append(
                {"command": list(command), "returncode": None, "error": str(error)}
            )
    return {
        "success": bool(results) and all(r["returncode"] == 0 for r in results),
        "commands": results,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for policy in ("full", "dependency"):
        runs = [r for r in rows if r["policy"] == policy]
        summary[policy] = {
            "runs": len(runs),
            "successful": sum(r["verification"]["success"] for r in runs),
            "infrastructure_errors": sum(r["status"] != "completed" for r in runs),
            **{
                "provider_" + field: sum(r[field] for r in runs)
                if runs and all(r.get(field) is not None for r in runs)
                else None
                for field in ("input_tokens", "cached_input_tokens", "output_tokens")
            },
            "provider_usage_complete": all(
                r.get("input_tokens") is not None and r.get("output_tokens") is not None
                for r in runs
            ),
            "median_wall_seconds": statistics.median(r["wall_seconds"] for r in runs)
            if runs
            else None,
            "live_evidence_calls": sum(r["live_evidence_calls"] for r in runs),
            "snapshot_expansions": sum(r["snapshot_expansions"] for r in runs),
        }
    summary["dollar_cost_usd"] = None
    summary["statistical_quality_preservation_claim"] = None
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--seed", type=int, default=731)
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("trials must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = Path(__file__).resolve().parents[1]
    count, _ = tokenizer("o200k_base")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    patch = subprocess.run(
        ["git", "diff", "HEAD"], cwd=project, capture_output=True, text=True, check=True
    ).stdout
    (output / "implementation.patch").write_text(patch, encoding="utf-8")
    report: dict[str, Any] = {
        "benchmark_kind": "live_mcp_paired_repository_patch_pilot",
        "implementation_commit": revision,
        "working_diff_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "model": args.model,
        "seed": args.seed,
        "trials_per_case": args.trials,
        "auxiliary_neural_model_used": False,
        "api_spend_initiated": False,
        "billing": "existing_chatgpt_subscription_usage_dollar_cost_unavailable",
        "encoding_for_context_counts": "o200k_base",
        "quality_claim": None,
        "rows": [],
        "status": "running",
    }
    rng = random.Random(args.seed)
    dump(output / "report.json", report)
    for case_path in args.case:
        manifest = load_manifest(case_path)
        if manifest.verification_patch:
            raise ValueError(
                "pilot requires verification commands without a hidden patch"
            )
        base = acquire_repository(manifest, output / (manifest.case_id + "-base"))
        for command in manifest.setup:
            subprocess.run(command, cwd=base, check=True, timeout=300)
        before = verify(base, manifest.verification)
        dump(output / (manifest.case_id + "-before.json"), before)
        if before["success"] or any(
            r["returncode"] is None for r in before["commands"]
        ):
            raise RuntimeError(
                "baseline task must fail mechanically without infrastructure errors"
            )
        for trial in range(args.trials):
            policies = ["full", "dependency"]
            rng.shuffle(policies)
            for policy in policies:
                run_dir = output / f"{manifest.case_id}-{trial}-{policy}"
                run_dir.mkdir()
                workspace = run_dir / "workspace"
                shutil.copytree(base, workspace)
                state = run_dir / "state"
                index = build_index(workspace, state / "index.sqlite")
                source_budget = 30000 if policy == "full" else 3000
                bundle = retrieve_evidence(
                    workspace,
                    manifest.task,
                    ReceiptStore(state / "receipts"),
                    budget=source_budget,
                    policy=policy,
                    index=index,
                    token_counter=count,
                    token_count_method="o200k_base",
                )
                dump(run_dir / "seed-evidence.json", bundle)
                prompt = (
                    "Resolve the following repository task. Make a minimal patch. "
                    "Use evidence_read or evidence_expand for missing implementation; "
                    "verify current hashes before editing source from a snapshot. "
                    "Before editing, call the ContextLens evidence_verify MCP tool "
                    "on a relevant initial span and evidence_read on its source range. "
                    "You may also search, read, edit and test normally with the shell. "
                    "Do not inspect parent directories or Git history. "
                    "Do not commit or push.\n\nTask:\n"
                    + manifest.task
                    + "\n\nInitial repository evidence:\n"
                    + json.dumps(bundle, ensure_ascii=False)
                )
                (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
                command = [
                    args.codex,
                    "exec",
                    "--ephemeral",
                    "--json",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--sandbox",
                    "workspace-write",
                    "--model",
                    args.model,
                    "-c",
                    'model_reasoning_effort="low"',
                    "-c",
                    "mcp_servers.contextlens.command=" + json.dumps(sys.executable),
                    "-c",
                    "mcp_servers.contextlens.args="
                    + json.dumps(
                        [
                            "-m",
                            "contextlens.pruning_cli",
                            "mcp",
                            "--root",
                            str(workspace),
                            "--state",
                            str(state),
                            "--encoding",
                            "o200k_base",
                            "--policy",
                            policy,
                        ]
                    ),
                    "-c",
                    'mcp_servers.contextlens.default_tools_approval_mode="approve"',
                    "-",
                ]
                if os.name == "nt":
                    command[-1:-1] = ["-c", 'windows.sandbox="elevated"']
                started = time.perf_counter()
                try:
                    completed = subprocess.run(
                        command,
                        cwd=workspace,
                        input=prompt,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=args.timeout,
                        env=isolated_agent_environment(),
                        check=False,
                    )
                    raw, stderr = completed.stdout, completed.stderr
                    status = (
                        "completed"
                        if completed.returncode == 0
                        else "agent_process_error"
                    )
                except subprocess.TimeoutExpired as error:
                    raw = (
                        error.stdout.decode("utf-8", errors="replace")
                        if isinstance(error.stdout, bytes)
                        else error.stdout or ""
                    )
                    stderr = str(error)
                    status = "timeout"
                (run_dir / "agent.jsonl").write_text(raw, encoding="utf-8")
                (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
                wall = time.perf_counter() - started
                parsed = _parse_jsonl(raw)
                if (
                    "blocked by read-only sandbox" in stderr
                    or "blocked by policy" in stderr
                ):
                    status = "invalid_sandbox_configuration"
                verification = verify(workspace, manifest.verification)
                dump(run_dir / "verification.json", verification)
                diff = subprocess.run(
                    ["git", "diff"],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=True,
                ).stdout
                (run_dir / "patch.diff").write_text(diff, encoding="utf-8")
                calls_path = state / "tool-calls.jsonl"
                calls = (
                    [json.loads(line) for line in calls_path.read_text().splitlines()]
                    if calls_path.exists()
                    else []
                )
                if status == "completed" and not all(
                    any(
                        c["operation"] == operation and not c.get("error")
                        for c in calls
                    )
                    for operation in ("verify", "read")
                ):
                    status = "invalid_live_tools_not_exercised"
                row = {
                    "case": manifest.case_id,
                    "repository_commit": manifest.commit,
                    "trial": trial,
                    "policy": policy,
                    "status": status,
                    "wall_seconds": wall,
                    "verification": verification,
                    "input_tokens": parsed.input_tokens,
                    "cached_input_tokens": parsed.cached_input_tokens,
                    "output_tokens": parsed.output_tokens,
                    "seed_source_tokens": bundle["source_tokens"],
                    "seed_response_tokens": count(
                        json.dumps(bundle, ensure_ascii=False)
                    ),
                    "live_evidence_calls": len(calls),
                    "snapshot_expansions": sum(
                        c["operation"] == "expand" for c in calls
                    ),
                    "tool_response_tokens": sum(c["response_tokens"] for c in calls),
                    "tool_latency_ms": sum(c["latency_ms"] for c in calls),
                    "agent_errors": parsed.errors,
                    "jsonl_parse_errors": parsed.parse_errors,
                    "patch_sha256": hashlib.sha256(diff.encode()).hexdigest(),
                }
                report["rows"].append(row)
                report["summary"] = summarize(report["rows"])
                report["paired_analysis"] = analyze_pairs(report["rows"])
                dump(output / "report.json", report)
                print(
                    json.dumps(
                        {
                            "case": row["case"],
                            "trial": trial,
                            "policy": policy,
                            "status": status,
                            "success": verification["success"],
                            "input_tokens": parsed.input_tokens,
                        }
                    ),
                    flush=True,
                )
                if status != "completed":
                    report["status"] = "invalid_infrastructure_failure"
                    dump(output / "report.json", report)
                    return 1
    report["status"] = "completed_pilot"
    dump(output / "report.json", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
