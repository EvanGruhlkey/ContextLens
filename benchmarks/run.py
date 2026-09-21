"""Paired coding-agent benchmark for the two ContextLens layers.

Three conditions run the same frozen tasks with the same model, reasoning
effort, tools, prompt, repository revision, timeout, and turn limit:

    baseline             raw tool output, transcript grows untouched
    live_pruning         large tool results pruned before the coding model
    live_and_compaction  live pruning plus periodic transcript compaction

Condition order is shuffled per task from a fixed seed. Verification happens on
a fresh checkout with hidden assertions the agent never sees.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.agent import CONDITIONS, DEFAULT_MAX_TURNS, run_agent
from benchmarks.report import analyze, markdown
from benchmarks.tasks import (
    Task,
    acquire_repository,
    apply_patch,
    head_commit,
    load_tasks,
    verify,
    working_patch,
)

SEED = 731


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def source_hash(project: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((project / "src").rglob("*.py")):
        digest.update(path.relative_to(project).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def grade(base: Path, destination: Path, diff: bytes, task: Task) -> dict[str, Any]:
    """Replay the agent's patch on a fresh checkout with frozen assertions."""

    shutil.copytree(
        base,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    if task.verification_patch:
        error = apply_patch(destination, task.verification_patch.encode())
        if error:
            return {"success": False, "commands": [], "patch_apply_error": error}
    error = apply_patch(destination, diff)
    if error:
        return {"success": False, "commands": [], "patch_apply_error": error}
    return verify(destination, task.verification)


def attempt(
    task: Task,
    base: Path,
    output: Path,
    trial: int,
    condition: str,
    *,
    model: str,
    timeout: int,
    max_turns: int,
) -> dict[str, Any]:
    run_dir = output / f"{task.case_id}-{trial}-{condition}"
    run_dir.mkdir(parents=True)
    workspace = run_dir / "workspace"
    shutil.copytree(base, workspace, ignore=shutil.ignore_patterns("__pycache__"))
    (run_dir / "prompt.txt").write_text(task.task, encoding="utf-8")
    metrics, transcript = run_agent(
        workspace,
        task.task,
        condition,
        state=run_dir / "state",
        model=model,
        timeout=timeout,
        max_turns=max_turns,
    )
    row = metrics.to_dict()
    dump(
        run_dir / "transcript.json",
        [
            {
                "role": message.role,
                "text": message.text,
                "tools": [use.tool for use in message.tool_uses],
                "results": [len(result.text) for result in message.tool_results],
            }
            for message in transcript
        ],
    )
    if head_commit(workspace) != task.commit:
        row["status"] = "invalid_repository_revision"
    diff = working_patch(workspace)
    (run_dir / "patch.diff").write_bytes(diff)
    verification_started = time.perf_counter()
    verification = grade(base, run_dir / "grading", diff, task)
    dump(run_dir / "verification.json", verification)
    if any(item["returncode"] is None for item in verification["commands"]):
        row["status"] = "verification_infrastructure_error"
    row.update(
        {
            "case": task.case_id,
            "repo": task.repo,
            "trial": trial,
            "condition": condition,
            "verified_success": row["status"] == "completed"
            and verification["success"],
            "patch_checks_passed": verification["success"],
            "verification_seconds": time.perf_counter() - verification_started,
            "patch_sha256": hashlib.sha256(diff).hexdigest(),
        }
    )
    dump(run_dir / "row.json", row)
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning", default="low")
    parser.add_argument(
        "--case", action="append", default=[], help="limit to these case ids"
    )
    arguments = parser.parse_args(argv)
    if min(arguments.trials, arguments.timeout, arguments.max_turns) < 1:
        parser.error("trials, timeout and max-turns must be positive")
    project = Path(__file__).resolve().parents[1]
    tasks = [
        task
        for task in load_tasks()
        if not arguments.case or task.case_id in set(arguments.case)
    ]
    if not tasks:
        parser.error("no frozen tasks selected")
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    expected_pairs = len(tasks) * arguments.trials
    report: dict[str, Any] = {
        "benchmark": "paired_coding_agent_contextlens_layers",
        "started_at": datetime.now(UTC).isoformat(),
        "implementation_sha256": source_hash(project),
        "implementation_revision": _revision(project),
        "model": arguments.model,
        "reasoning": arguments.reasoning,
        "trials": arguments.trials,
        "timeout": arguments.timeout,
        "max_turns": arguments.max_turns,
        "order_seed": SEED,
        "conditions": list(CONDITIONS),
        "integration": (
            "ContextLens runs on the host tool-response path and on the host "
            "transcript; the agent prompt never mentions it"
        ),
        "usage_definition": (
            "coding-model input includes cached input; uncached = input - "
            "cached; total = input + output. Jev tokens are separate."
        ),
        "tasks": [task.public_value() for task in tasks],
        "preflights": [],
        "rows": [],
        "status": "preflight",
    }
    dump(output / "report.json", report)
    (output / "driver-source.py").write_bytes(Path(__file__).read_bytes())

    bases: dict[str, Path] = {}
    for task in tasks:
        base = acquire_repository(task, output / f"{task.case_id}-base")
        before = verify(base, task.verification)
        gold = task.gold_patch.read_bytes()
        after = grade(base, output / f"{task.case_id}-calibration", gold, task)
        report["preflights"].append(
            {
                "case": task.case_id,
                "fails_before_fix": not before["success"],
                "passes_with_gold_patch": after["success"],
                "gold_patch_sha256": hashlib.sha256(gold).hexdigest(),
            }
        )
        dump(output / "report.json", report)
        if (
            before["success"]
            or not after["success"]
            or any(item["returncode"] is None for item in before["commands"])
        ):
            raise RuntimeError(f"invalid verification calibration: {task.case_id}")
        bases[task.case_id] = base

    report["status"] = "running"
    dump(output / "report.json", report)
    rng = random.Random(SEED)
    for task in tasks:
        for trial in range(arguments.trials):
            order = list(CONDITIONS)
            rng.shuffle(order)
            for condition in order:
                row = attempt(
                    task,
                    bases[task.case_id],
                    output,
                    trial,
                    condition,
                    model=arguments.model,
                    timeout=arguments.timeout,
                    max_turns=arguments.max_turns,
                )
                report["rows"].append(row)
                report["analysis"] = analyze(report["rows"], expected_pairs)
                dump(output / "report.json", report)
                print(
                    json.dumps(
                        {
                            key: row[key]
                            for key in (
                                "case",
                                "condition",
                                "status",
                                "verified_success",
                                "input_tokens",
                                "injected_tool_output_tokens",
                            )
                        }
                    ),
                    flush=True,
                )
    report["status"] = (
        "completed"
        if all(row["status"] == "completed" for row in report["rows"])
        else "completed_with_invalid_attempts"
    )
    report["completed_at"] = datetime.now(UTC).isoformat()
    dump(output / "report.json", report)
    (output / "README.md").write_text(markdown(report), encoding="utf-8")
    return 0


def _revision(project: Path) -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() or "unknown"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
