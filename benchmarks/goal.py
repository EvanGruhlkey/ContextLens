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
from benchmarks.host_agent import (
    CODING_POLICIES,
    DEFAULT_MAX_TURNS,
    coding_prompt,
    run_host_agent,
)
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
    if policy in {"compact", "control"}:
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
                    "jev" if policy == "control" else "compact",
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
        if policy == "control":
            command += [
                "-c",
                'mcp_servers.contextlens.env_vars=["AI_GATEWAY_API_KEY"]',
            ]
    elif policy != "normal":
        raise ValueError("unknown benchmark policy")
    if os.name == "nt":
        command += ["-c", 'windows.sandbox="elevated"']
    return command + ["-"]


def prompt(task: str, policy: str) -> str:
    text = coding_prompt(task)
    if policy in CODING_POLICIES or policy == "normal":
        return text
    if policy == "compact":
        text += (
            "\n\nUse the ContextLens MCP context_find/context_read tools for "
            "repository discovery and source reads. Discover these tools through "
            "tool search if needed. Read known paths directly; find only when "
            "needed. Use normal tools for edits and tests, and unsupported reads. "
            "If ContextLens cannot provide needed context, recover or fall back "
            "and explain why. Do not skip ContextLens entirely."
        )
    elif policy == "control":
        text += (
            "\n\nUse the ContextLens MCP tools throughout the investigation. "
            "Call context_next with two to twelve concrete candidate actions "
            "before each major search, read, test, edit, or stop decision, then "
            "follow the selected capability. Actions are objects such as "
            '{"id":"search","kind":"search_repository","description":'
            '"Search for the symbol","tool":"context_select"}; never pass '
            "plain strings. Save concise search, test, diff, and "
            "failure results with context_observe so the next decision uses the "
            "current working set. Observation types are source, search_result, "
            "test_output, traceback, diff, tool_result, configuration, and "
            "user_constraint. Use context_select/context_read for repository "
            "evidence and context_recall when deferred evidence is needed. Normal "
            "tools still own edits, command arguments, and execution."
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
    if manifest.verification_patch:
        hidden = subprocess.run(
            ["git", "apply", "--binary", "-"],
            input=manifest.verification_patch.encode(),
            cwd=destination,
            capture_output=True,
            check=False,
        )
        if hidden.returncode:
            return {
                "success": False,
                "commands": [],
                "patch_apply_error": hidden.stderr.decode(errors="replace"),
            }
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


def attempt_coding(
    manifest: RepositoryCaseManifest,
    base: Path,
    output: Path,
    trial: int,
    policy: str,
    model: str,
    timeout: int,
    max_turns: int,
) -> dict[str, Any]:
    run_dir = output / f"{manifest.case_id}-{trial}-{policy}"
    run_dir.mkdir()
    workspace = run_dir / "workspace"
    shutil.copytree(base, workspace, ignore=shutil.ignore_patterns("__pycache__"))
    environment = isolated_agent_environment()
    for name in ("OPENAI_API_KEY", "AI_GATEWAY_API_KEY", "OPENAI_BASE_URL"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment["PYTHONPATH"] = str(output / "snapshot" / "src")
    environment["PATH"] = (
        str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
    )
    text = prompt(manifest.task, policy)
    (run_dir / "prompt.txt").write_text(text, encoding="utf-8")
    previous = dict(os.environ)
    os.environ.update(environment)
    started = time.perf_counter()
    try:
        result = run_host_agent(
            workspace,
            manifest.task,
            policy,
            model=model,
            timeout=timeout,
            max_turns=max_turns,
            state=run_dir / "state",
        )
    finally:
        os.environ.clear()
        os.environ.update(previous)
    result["agent_seconds"] = time.perf_counter() - started
    dump(run_dir / "history.json", result.pop("history", []))
    status = result["status"]
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
    raw = result.get("raw_tool_output_tokens") or 0
    injected = result.get("injected_tool_output_tokens") or 0
    removed = result.get("tool_output_tokens_removed")
    if removed is None:
        removed = max(0, raw - injected)
    row = {
        "case": manifest.case_id,
        "repo": manifest.repo,
        "trial": trial,
        "policy": policy,
        "status": status,
        "verified_success": status == "completed" and verification["success"],
        "patch_checks_passed": verification["success"],
        "verification": verification,
        "input_tokens": result.get("input_tokens"),
        "cached_input_tokens": result.get("cached_input_tokens"),
        "uncached_input_tokens": result.get("uncached_input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "total_tokens": result.get("total_tokens"),
        "agent_turns": result.get("agent_turns") or 0,
        "raw_tool_output_tokens": raw,
        "injected_tool_output_tokens": injected,
        "tool_output_tokens_removed": removed,
        "tool_output_reduction_percent": result.get("tool_output_reduction_percent")
        or 0.0,
        "filter_calls": result.get("filter_calls") or 0,
        "recovery_calls": result.get("recovery_calls") or 0,
        "recovered_tokens": result.get("recovered_tokens") or 0,
        "jev_input_tokens": result.get("jev_input_tokens") or 0,
        "jev_output_tokens": result.get("jev_output_tokens") or 0,
        "jev_cost": result.get("jev_cost") or "0",
        "agent_seconds": result["agent_seconds"],
        "verification_seconds": verification_seconds,
        "contextlens_calls": result.get("filter_calls") or 0,
        "contextlens_read_calls": 0,
        "contextlens_returned_tokens": injected,
        "controller_calls": 0,
        "shell_calls": 0,
        "patch_sha256": hashlib.sha256(diff).hexdigest(),
        "agent_errors": [],
        "parse_errors": [],
    }
    dump(run_dir / "row.json", row)
    return row


def attempt(
    manifest: RepositoryCaseManifest,
    base: Path,
    output: Path,
    trial: int,
    policy: str,
    model: str,
    codex: str,
    timeout: int,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> dict[str, Any]:
    if policy in CODING_POLICIES:
        return attempt_coding(
            manifest,
            base,
            output,
            trial,
            policy,
            model,
            timeout,
            max_turns,
        )
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
    controller_path = run_dir / "state" / "controller_calls.jsonl"
    controller_calls = (
        [
            json.loads(line)
            for line in controller_path.read_text(encoding="utf-8").splitlines()
        ]
        if controller_path.exists()
        else []
    )
    if policy == "compact" and status == "completed" and not exact_reads:
        status = "invalid_contextlens_not_used"
    if policy == "control" and status == "completed" and not controller_calls:
        status = "invalid_contextlens_not_used"
    jev_input_tokens = sum(call.get("input_tokens") or 0 for call in controller_calls)
    jev_output_tokens = sum(call.get("output_tokens") or 0 for call in controller_calls)
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
        "controller_calls": len(controller_calls),
        "jev_input_tokens": jev_input_tokens,
        "jev_output_tokens": jev_output_tokens,
        "shell_calls": len(parsed.command_events),
        "patch_sha256": hashlib.sha256(diff).hexdigest(),
        "agent_errors": parsed.errors,
        "parse_errors": parsed.parse_errors,
    }
    dump(run_dir / "row.json", row)
    return row


def analyze(
    rows: list[dict[str, Any]],
    expected_pairs: int,
    *,
    candidate_policy: str = "compact",
) -> dict[str, Any]:
    policies = ("normal", candidate_policy)
    conditions = {}
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
        "total_tokens",
    )
    for policy in policies:
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
            "controller_calls": sum(row.get("controller_calls", 0) for row in selected),
            "jev_input_tokens": sum(row.get("jev_input_tokens", 0) for row in selected),
            "jev_output_tokens": sum(
                row.get("jev_output_tokens", 0) for row in selected
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
            for policy in policies
        )
    ]
    first = sum(pair["normal"]["input_tokens"] for pair in pairs)
    second = sum(pair[candidate_policy]["input_tokens"] for pair in pairs)
    complete_first = first + sum(
        pair["normal"].get("jev_input_tokens", 0) for pair in pairs
    )
    complete_second = second + sum(
        pair[candidate_policy].get("jev_input_tokens", 0) for pair in pairs
    )
    regressions = [
        pair["normal"]["case"]
        for pair in pairs
        if pair["normal"]["verified_success"]
        and not pair[candidate_policy]["verified_success"]
    ]
    complete = len(pairs) == expected_pairs and len(rows) == expected_pairs * 2
    read_uptake = all(
        pair[candidate_policy]["contextlens_read_calls"] > 0 for pair in pairs
    )
    controller_uptake = all(
        pair[candidate_policy].get("controller_calls", 0) > 0 for pair in pairs
    )
    uptake = controller_uptake if candidate_policy == "control" else read_uptake
    return {
        "conditions": conditions,
        "complete_pairs": len(pairs),
        "expected_pairs": expected_pairs,
        "paired_gross_input_reduction_percent": round((1 - second / first) * 100, 2)
        if first
        else None,
        "paired_complete_input_reduction_percent": round(
            (1 - complete_second / complete_first) * 100, 2
        )
        if complete_first
        else None,
        "observed_quality_regressions": regressions,
        "all_candidate_runs_used_contextlens_reads": bool(pairs) and read_uptake,
        "all_candidate_runs_used_controller": bool(pairs)
        and controller_uptake
        and candidate_policy == "control",
        "observed_sample_meets_goal": complete_second < complete_first
        and all(pair[candidate_policy]["verified_success"] for pair in pairs)
        and not regressions
        if complete and uptake
        else None,
        "statistical_quality_preservation_claim": None,
        "dollar_cost_usd": None,
    }


def comma(value: int | float | None) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}"


def _sum_field(rows: list[dict[str, Any]], field: str) -> int | None:
    if not rows or any(row.get(field) is None for row in rows):
        return None
    if field == "jev_cost":
        total = 0.0
        for row in rows:
            total += float(row.get("jev_cost") or 0)
        return int(total) if total == int(total) else total  # type: ignore[return-value]
    return sum(row[field] for row in rows)


def analyze_coding(
    rows: list[dict[str, Any]],
    expected_pairs: int,
) -> dict[str, Any]:
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
        "total_tokens",
        "agent_turns",
        "raw_tool_output_tokens",
        "injected_tool_output_tokens",
        "tool_output_tokens_removed",
        "filter_calls",
        "recovery_calls",
        "recovered_tokens",
        "jev_input_tokens",
        "jev_output_tokens",
        "agent_seconds",
    )
    conditions = {}
    for policy in CODING_POLICIES:
        selected = [row for row in rows if row["policy"] == policy]
        jev_cost = 0.0
        for row in selected:
            try:
                jev_cost += float(row.get("jev_cost") or 0)
            except (TypeError, ValueError):
                jev_cost += 0.0
        conditions[policy] = {
            "attempts": len(selected),
            "passed": sum(bool(row["verified_success"]) for row in selected),
            **{
                field: _sum_field(selected, field)
                if field != "agent_seconds"
                else (
                    statistics.median(row["agent_seconds"] for row in selected)
                    if selected
                    else None
                )
                for field in fields
            },
            "jev_cost": format(jev_cost, "f"),
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
        if all(policy in group for policy in CODING_POLICIES)
    ]
    tasks = []
    for group in pairs:
        baseline = group["baseline"]
        full = group["contextlens"]
        base_input = baseline.get("input_tokens")
        full_input = full.get("input_tokens")
        saved = (
            base_input - full_input
            if isinstance(base_input, int) and isinstance(full_input, int)
            else None
        )
        change = (
            round(100 * saved / base_input, 1)
            if saved is not None and base_input
            else None
        )
        tasks.append(
            {
                "task": f"{baseline.get('repo', '')}/{baseline['case']}",
                "case": baseline["case"],
                "baseline_pass": baseline["verified_success"],
                "contextlens_pass": full["verified_success"],
                "jev_pass": group["jev_filter"]["verified_success"],
                "baseline_input": base_input,
                "contextlens_input": full_input,
                "tokens_saved": saved,
                "input_change_percent": change,
                "status": {
                    policy: group[policy]["status"] for policy in CODING_POLICIES
                },
            }
        )

    def delta(field: str, policy: str) -> dict[str, Any]:
        first = conditions["baseline"].get(field)
        second = conditions[policy].get(field)
        if not isinstance(first, int) or not isinstance(second, int):
            return {"absolute": None, "percent": None}
        absolute = second - first
        percent = round(100 * absolute / first, 1) if first else None
        return {"absolute": absolute, "percent": percent}

    complete = len(pairs) == expected_pairs and len(rows) == expected_pairs * 3
    return {
        "conditions": conditions,
        "complete_pairs": len(pairs),
        "expected_pairs": expected_pairs,
        "complete": complete,
        "deltas": {
            "jev_filter": {field: delta(field, "jev_filter") for field in fields},
            "contextlens": {field: delta(field, "contextlens") for field in fields},
        },
        "tasks": tasks,
        "statistical_quality_preservation_claim": None,
        "dollar_cost_usd": None,
    }


def mark(passed: bool) -> str:
    return "✅" if passed else "❌"


def markdown_report(analysis: dict[str, Any]) -> str:
    conditions = analysis["conditions"]
    labels = {
        "baseline": "Baseline",
        "jev_filter": "Jev Filter",
        "contextlens": "ContextLens",
    }
    columns = (
        ("passed", "Verified Fixes"),
        ("input_tokens", "Coding Input Tokens"),
        ("uncached_input_tokens", "Uncached Input"),
        ("cached_input_tokens", "Cached Input"),
        ("output_tokens", "Output Tokens"),
        ("total_tokens", "Total Coding Tokens"),
        ("raw_tool_output_tokens", "Raw Tool Output"),
        ("injected_tool_output_tokens", "Injected Tool Output"),
        ("tool_output_tokens_removed", "Tokens Removed"),
        ("agent_turns", "Agent Turns"),
        ("recovery_calls", "Recoveries"),
        ("jev_input_tokens", "Jev Input"),
    )
    overall_header = (
        "| Condition   | Verified Fixes | Coding Input Tokens | Uncached Input | "
        "Cached Input | Output Tokens | Total Coding Tokens | Raw Tool Output | "
        "Injected Tool Output | Tokens Removed | Agent Turns | Recoveries | "
        "Jev Input |"
    )
    overall_rule = (
        "| ----------- | -------------: | ------------------: | -------------: | "
        "-----------: | ------------: | ------------------: | --------------: | "
        "-------------------: | -------------: | ----------: | ---------: | "
        "--------: |"
    )
    lines = [overall_header, overall_rule]
    for policy in CODING_POLICIES:
        row = conditions[policy]
        cells = [labels[policy].ljust(11)]
        for field, _title in columns:
            cells.append(comma(row.get(field)))
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "| Metric                      | Jev Filter vs Baseline | "
        "ContextLens vs Baseline |",
        "| --------------------------- | ---------------------: | "
        "----------------------: |",
    ]
    metric_names = (
        ("input_tokens", "Coding-model input tokens"),
        ("uncached_input_tokens", "Uncached coding-model input"),
        ("injected_tool_output_tokens", "Injected tool output"),
        ("agent_turns", "Agent turns"),
        ("passed", "Verified fixes"),
    )
    for field, title in metric_names:
        cells = [title.ljust(27)]
        for policy in ("jev_filter", "contextlens"):
            if field == "passed":
                first = conditions["baseline"]["passed"]
                second = conditions[policy]["passed"]
                absolute = second - first
                cell = f"{absolute:+,}"
            else:
                item = analysis["deltas"][policy][field]
                absolute = item["absolute"]
                percent = item["percent"]
                if absolute is None:
                    cell = "n/a"
                else:
                    sign = f"{absolute:+,}"
                    cell = f"{sign} ({percent:+.1f}%)" if percent is not None else sign
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "| Task      | Baseline Pass | ContextLens Pass | Baseline Input | "
        "ContextLens Input | Tokens Saved | Input Change |",
        "| --------- | ------------- | ---------------- | -------------: | "
        "----------------: | -----------: | -----------: |",
    ]
    for task in analysis["tasks"]:
        change = task["input_change_percent"]
        change_text = f"{change:+.1f}%" if change is not None else "n/a"
        lines.append(
            "| "
            + " | ".join(
                [
                    task["task"],
                    mark(task["baseline_pass"]),
                    mark(task["contextlens_pass"]),
                    comma(task["baseline_input"]),
                    comma(task["contextlens_input"]),
                    comma(task["tokens_saved"]),
                    change_text,
                ]
            )
            + " |"
        )
    baseline = conditions["baseline"]
    full = conditions["contextlens"]
    jev = conditions["jev_filter"]
    input_delta = analysis["deltas"]["contextlens"]["input_tokens"]
    uncached_delta = analysis["deltas"]["contextlens"]["uncached_input_tokens"]
    tool_delta = analysis["deltas"]["contextlens"]["injected_tool_output_tokens"]
    turn_delta = analysis["deltas"]["contextlens"]["agent_turns"]
    turns_text = "n/a"
    if turn_delta["absolute"] is not None:
        if turn_delta["absolute"] > 0:
            turns_text = (
                f"increased by {comma(turn_delta['absolute'])} "
                f"({turn_delta['percent']:+.1f}%)"
            )
        elif turn_delta["absolute"] < 0:
            turns_text = (
                f"decreased by {comma(-turn_delta['absolute'])} "
                f"({turn_delta['percent']:+.1f}%)"
            )
        else:
            turns_text = "did not change"
    lines += [
        "",
        "Verified fixes: "
        f"Baseline {comma(baseline['passed'])}/{comma(baseline['attempts'])}, "
        f"Jev Filter {comma(jev['passed'])}/{comma(jev['attempts'])}, "
        f"ContextLens {comma(full['passed'])}/{comma(full['attempts'])}.",
        "",
        (
            f"ContextLens saved {comma(-(input_delta['absolute'] or 0))} coding-model "
            f"input tokens"
            + (
                f" ({input_delta['percent']:+.1f}%)."
                if input_delta["percent"] is not None
                else "."
            )
        ),
        (
            f"Uncached coding-model input changed by "
            f"{comma(uncached_delta['absolute'])}"
            + (
                f" ({uncached_delta['percent']:+.1f}%)."
                if uncached_delta["percent"] is not None
                else "."
            )
        ),
        (
            f"Tool-output tokens prevented from entering model context: "
            f"{comma(-(tool_delta['absolute'] or 0))} "
            f"(injected {comma(full['injected_tool_output_tokens'])} vs baseline "
            f"{comma(baseline['injected_tool_output_tokens'])})."
        ),
        f"Agent turns {turns_text}.",
        (
            f"Recoveries: {comma(full['recovery_calls'])} calls restoring "
            f"{comma(full['recovered_tokens'])} tokens."
        ),
        (
            f"Jev usage is separate from the coding model: "
            f"{comma(full['jev_input_tokens'])} input / "
            f"{comma(full['jev_output_tokens'])} output tokens, "
            f"cost {full['jev_cost']}."
        ),
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--codex", default="codex")
    parser.add_argument(
        "--candidate-policy",
        choices=("coding", "compact", "control"),
        default="coding",
    )
    args = parser.parse_args()
    if min(args.trials, args.timeout, args.max_turns) < 1:
        parser.error("trials, timeout and max-turns must be positive")
    project = Path(__file__).resolve().parents[1]
    coding = args.candidate_policy == "coding"
    fixtures = project / "benchmarks" / "fixtures" / (
        "coding" if coding else "goal"
    )
    manifests = [load_manifest(path) for path in sorted(fixtures.glob("*.json"))]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(
        project / "src",
        output / "snapshot" / "src",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    report: dict[str, Any] = {
        "benchmark": (
            "paired_coding_agent_filter"
            if coding
            else f"paired_{args.candidate_policy}_mcp_agent_pilot"
        ),
        "started_at": datetime.now(UTC).isoformat(),
        "implementation_sha256": source_hash(project),
        "implementation_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip(),
        "model": args.model,
        "reasoning": "low",
        "trials": args.trials,
        "max_turns": args.max_turns,
        "order_seed": 731,
        "integration": (
            "transparent ContextAdapter filter on host tools; agent is not told "
            "to use ContextLens"
            if coding
            else (
                f"{args.candidate_policy} MCP; native tools available; "
                "callback adapter not used"
            )
        ),
        "usage_definition": (
            "coding-model input includes cached input; uncached=input-cached; "
            "total=input+output. Jev tokens are recorded separately."
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
    policies = list(CODING_POLICIES) if coding else ["normal", args.candidate_policy]
    expected_pairs = len(manifests) * args.trials
    for manifest in manifests:
        for trial in range(args.trials):
            ordered = list(policies)
            rng.shuffle(ordered)
            for policy in ordered:
                row = attempt(
                    manifest,
                    bases[manifest.case_id],
                    output,
                    trial,
                    policy,
                    args.model,
                    args.codex,
                    args.timeout,
                    max_turns=args.max_turns,
                )
                if coding:
                    row["agent_turns"] = row.get("agent_turns") or 0
                report["rows"].append(row)
                report["analysis"] = (
                    analyze_coding(report["rows"], expected_pairs)
                    if coding
                    else analyze(
                        report["rows"],
                        expected_pairs,
                        candidate_policy=args.candidate_policy,
                    )
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
                            if key in row
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
    if coding:
        (output / "README.md").write_text(
            markdown_report(report["analysis"]), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
