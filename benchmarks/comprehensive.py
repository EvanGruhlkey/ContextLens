"""Four-condition, resumable real-repository agent evaluation.

Task checks remain outside the agent workspace. Public historical tasks are a
convenience sample, not contamination-free evidence of general non-inferiority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.evidence_agent import dump, isolated_agent_environment, verify
from contextlens.evidence import retrieve_evidence
from contextlens.evidence_index import build_index
from contextlens.evidence_session import tokenizer
from contextlens.experiments.codex_cli import _parse_jsonl
from contextlens.pruning import ReceiptStore
from evals.repository_cases import (
    RepositoryCaseManifest,
    acquire_repository,
    load_manifest,
)

POLICIES = ("normal", "full", "lexical", "dependency")


def source_hash(project: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((project / "src").rglob("*.py")):
        digest.update(path.relative_to(project).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def command_for(
    policy: str, workspace: Path, state: Path, model: str, codex: str
) -> list[str]:
    command = [
        codex,
        "exec",
        "--ephemeral",
        "--json",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "workspace-write",
        "--model",
        model,
        "-c",
        'model_reasoning_effort="low"',
    ]
    if policy != "normal":
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
        ]
    if os.name == "nt":
        command += ["-c", 'windows.sandbox="elevated"']
    return command + ["-"]


def run_attempt(
    manifest: RepositoryCaseManifest,
    base: Path,
    output: Path,
    policy: str,
    trial: int,
    model: str,
    codex: str,
    timeout: int,
) -> dict[str, Any]:
    run_dir = output / f"{manifest.case_id}-{trial}-{policy}"
    run_dir.mkdir(exist_ok=True)
    workspace = run_dir / "workspace"
    if workspace.exists():
        raise RuntimeError(
            "unfinished workspace exists; preserve it and use new output"
        )
    shutil.copytree(base, workspace)
    state = run_dir / "state"
    count, method = tokenizer("o200k_base")
    started = time.perf_counter()
    bundle = None
    if policy != "normal":
        index = build_index(workspace, state / "index.sqlite")
        bundle = retrieve_evidence(
            workspace,
            manifest.task,
            ReceiptStore(state / "receipts"),
            index=index,
            budget=30000 if policy == "full" else 3000,
            policy=policy,
            token_counter=count,
            token_count_method=method,
        )
        dump(run_dir / "seed-evidence.json", bundle)
    preparation_seconds = time.perf_counter() - started
    prompt = (
        "Resolve the following repository task. Make a minimal patch. "
        "Search, read, edit and test with the available tools. "
        "Do not inspect parent directories or Git history. Do not commit or push.\n\n"
        "Task:\n" + manifest.task
    )
    if bundle is not None:
        prompt += (
            "\n\nContextLens tools are available for source reads and recovery. "
            "Verify source hashes before editing old snapshots. Before editing, call "
            "evidence_verify on a relevant initial span and evidence_read on its range."
            "\n\nInitial repository evidence:\n"
            + json.dumps(bundle, ensure_ascii=False)
        )
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    environment = isolated_agent_environment()
    environment["PYTHONPATH"] = str(output / "snapshot" / "src")
    agent_started = time.perf_counter()
    try:
        completed = subprocess.run(
            command_for(policy, workspace, state, model, codex),
            cwd=workspace,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
            check=False,
        )
        raw, stderr = completed.stdout, completed.stderr
        status = "completed" if completed.returncode == 0 else "agent_process_error"
    except subprocess.TimeoutExpired as error:
        raw = error.stdout or ""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        stderr, status = str(error), "timeout"
    agent_seconds = time.perf_counter() - agent_started
    (run_dir / "agent.jsonl").write_text(raw, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    parsed = _parse_jsonl(raw)
    if any(
        marker in (raw + stderr).lower()
        for marker in ("usage_limit_reached", "you've hit your usage limit")
    ):
        status = "subscription_limit_reached"
    if "blocked by read-only sandbox" in stderr:
        status = "invalid_sandbox_configuration"
    verification_started = time.perf_counter()
    verification = verify(workspace, manifest.verification)
    verification_seconds = time.perf_counter() - verification_started
    dump(run_dir / "verification.json", verification)
    if any(c["returncode"] is None for c in verification["commands"]):
        status = "verification_infrastructure_error"
    calls_path = state / "tool-calls.jsonl"
    calls = (
        [json.loads(line) for line in calls_path.read_text().splitlines()]
        if calls_path.exists()
        else []
    )
    if (
        policy != "normal"
        and status == "completed"
        and not all(
            any(c["operation"] == operation and not c.get("error") for c in calls)
            for operation in ("verify", "read")
        )
    ):
        status = "invalid_live_tools_not_exercised"
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
    row = {
        "case": manifest.case_id,
        "repo": manifest.repo,
        "repository_commit": manifest.commit,
        "trial": trial,
        "policy": policy,
        "status": status,
        "verification": verification,
        "input_tokens": parsed.input_tokens,
        "cached_input_tokens": parsed.cached_input_tokens,
        "output_tokens": parsed.output_tokens,
        "preparation_seconds": preparation_seconds,
        "agent_wall_seconds": agent_seconds,
        "verification_seconds": verification_seconds,
        "wall_seconds": preparation_seconds + agent_seconds + verification_seconds,
        "seed_source_tokens": bundle["source_tokens"] if bundle else 0,
        "seed_response_tokens": count(json.dumps(bundle, ensure_ascii=False))
        if bundle
        else 0,
        "live_evidence_calls": len(calls),
        "snapshot_expansions": sum(c["operation"] == "expand" for c in calls),
        "tool_response_tokens": sum(c["response_tokens"] for c in calls),
        "agent_errors": parsed.errors,
        "jsonl_parse_errors": parsed.parse_errors,
        "patch_sha256": hashlib.sha256(diff.encode()).hexdigest(),
    }
    dump(run_dir / "row.json", row)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if min(args.trials, args.workers, args.timeout) < 1:
        parser.error("trials, workers and timeout must be positive")
    project = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    manifests = [load_manifest(p) for p in args.case]
    if len({m.case_id for m in manifests}) != len(manifests):
        parser.error("duplicate case IDs")
    protocol = {
        "model": args.model,
        "trials": args.trials,
        "workers": args.workers,
        "timeout": args.timeout,
        "policies": list(POLICIES),
        "seed": 731,
        "source_sha256": source_hash(project),
        "manifests": [m.public_value() for m in manifests],
    }
    if args.resume:
        report = json.loads((output / "report.json").read_text())
        if report["protocol"] != protocol:
            raise ValueError("resume protocol or source differs; use new output")
        # Recover rows atomically saved by a worker before the controller stopped.
        recovered = {(r["case"], r["trial"], r["policy"]): r for r in report["rows"]}
        for path in output.glob("*/row.json"):
            row = json.loads(path.read_text())
            recovered[(row["case"], row["trial"], row["policy"])] = row
        report["rows"] = list(recovered.values())
        retries = [
            r for r in report["rows"] if r["status"] == "subscription_limit_reached"
        ]
        report.setdefault("infrastructure_retry_history", []).extend(retries)
        report["rows"] = [r for r in report["rows"] if r not in retries]
        for row in retries:
            previous = output / f"{row['case']}-{row['trial']}-{row['policy']}"
            archive = output / (previous.name + "-limit-" + str(time.time_ns()))
            if not previous.resolve().is_relative_to(output):
                raise ValueError("retry path escaped the benchmark output")
            previous.rename(archive)
    else:
        output.mkdir(parents=True, exist_ok=False)
        shutil.copytree(
            project / "src",
            output / "snapshot" / "src",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        report = {
            "benchmark_kind": "four_condition_repository_agent_evaluation",
            "protocol": protocol,
            "started_at": datetime.now(UTC).isoformat(),
            "api_spend_initiated": False,
            "dollar_cost_usd": None,
            "quality_preservation_claim": None,
            "rows": [],
            "preflights": [],
            "status": "preflight",
        }
        (output / "driver-source.py").write_text(
            Path(__file__).read_text(), encoding="utf-8"
        )
        dump(output / "report.json", report)
    bases = {}
    for manifest in manifests:
        if manifest.verification_patch:
            raise ValueError("hidden patches unsupported; use external commands")
        base = output / (manifest.case_id + "-base")
        if not base.exists():
            acquire_repository(manifest, base)
            for command in manifest.setup:
                subprocess.run(command, cwd=base, check=True, timeout=300)
        if not any(p["case"] == manifest.case_id for p in report["preflights"]):
            before = verify(base, manifest.verification)
            report["preflights"].append(
                {"case": manifest.case_id, "verification": before}
            )
            dump(output / "report.json", report)
            if before["success"] or any(
                c["returncode"] is None for c in before["commands"]
            ):
                raise RuntimeError("case must fail before patch without infra failure")
        bases[manifest.case_id] = base
    done = {(r["case"], r["trial"], r["policy"]) for r in report["rows"]}
    jobs = [
        (m, trial, policy)
        for m in manifests
        for trial in range(args.trials)
        for policy in POLICIES
        if (m.case_id, trial, policy) not in done
    ]
    random.Random(731).shuffle(jobs)
    report["status"] = "running"
    dump(output / "report.json", report)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                run_attempt,
                m,
                bases[m.case_id],
                output,
                policy,
                trial,
                args.model,
                args.codex,
                args.timeout,
            ): (m.case_id, trial, policy)
            for m, trial, policy in jobs
        }
        for future in as_completed(futures):
            if future.cancelled():
                continue
            row = future.result()
            report["rows"].append(row)
            dump(output / "report.json", report)
            print(
                json.dumps(
                    {k: row[k] for k in ("case", "trial", "policy", "status")}
                    | {
                        "success": row["verification"]["success"],
                        "finished": len(report["rows"]),
                    }
                ),
                flush=True,
            )
            if row["status"] == "subscription_limit_reached":
                for pending in futures:
                    pending.cancel()
                report["status"] = "subscription_limit_reached"
                dump(output / "report.json", report)
    if report["status"] == "subscription_limit_reached":
        return 1
    report["status"] = (
        "completed"
        if all(r["status"] == "completed" for r in report["rows"])
        else "completed_with_invalid_runs"
    )
    report["completed_at"] = datetime.now(UTC).isoformat()
    dump(output / "report.json", report)
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
