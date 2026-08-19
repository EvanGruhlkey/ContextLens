"""Reproducible external-repository preparation, grading, and agent trials."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contextlens.experiments import ExperimentEvent
from contextlens.minimize import build_minimization_candidate
from contextlens.regression import (
    render_verification_markdown,
    verify_repository,
)
from contextlens.repository import scan_repository

HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "cases.json"
CANDIDATE_SPECS_PATH = HERE / "candidate-specs.json"
DEFAULT_CACHE = Path(
    os.environ.get(
        "CONTEXTLENS_CASE_CACHE",
        Path(tempfile.gettempdir()) / "contextlens-case-studies",
    )
)


def load_agent_studies(path: Path = MANIFEST_PATH) -> tuple[dict[str, Any], ...]:
    value = json.loads(path.read_text(encoding="utf-8"))
    studies = value.get("agent_studies")
    if not isinstance(studies, list) or not studies:
        raise ValueError("case manifest has no agent_studies")
    return tuple(_object(item, "agent study") for item in studies)


def selected_task(
    study_id: str,
    task_id: str,
    *,
    path: Path = MANIFEST_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    for study in load_agent_studies(path):
        if study.get("id") != study_id:
            continue
        for raw_task in _sequence(study.get("tasks"), "tasks"):
            task = _object(raw_task, "task")
            if task.get("id") == task_id:
                return study, task
        raise ValueError(f"unknown task {task_id!r} for study {study_id!r}")
    raise ValueError(f"unknown study {study_id!r}")


def remove_bounded_block(
    content: str,
    *,
    start: str,
    end: str,
) -> str:
    start_index = content.find(start)
    end_index = content.find(end)
    if start_index < 0 or end_index < start_index:
        raise ValueError(f"bounded candidate markers not found: {start!r}, {end!r}")
    end_index += len(end)
    while end_index < len(content) and content[end_index] in "\r\n":
        end_index += 1
    return content[:start_index].rstrip() + "\n" + content[end_index:]


def remove_markdown_sections(content: str, headings: Sequence[str]) -> str:
    lines = content.splitlines(keepends=True)
    selected = {heading.strip() for heading in headings}
    output: list[str] = []
    skipping_level: int | None = None
    found: set[str] = set()
    for line in lines:
        stripped = line.strip()
        level = len(stripped) - len(stripped.lstrip("#"))
        is_heading = level > 0 and stripped[level : level + 1] == " "
        if is_heading and stripped in selected:
            found.add(stripped)
            skipping_level = level
            continue
        if skipping_level is not None:
            if is_heading and level <= skipping_level:
                skipping_level = None
            else:
                continue
        output.append(line)
    missing = selected - found
    if missing:
        raise ValueError(f"candidate headings not found: {sorted(missing)}")
    return "".join(output)


def prepare_candidate(
    workspace: Path,
    study: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    specs_path: Path = CANDIDATE_SPECS_PATH,
) -> dict[str, Any]:
    specs = json.loads(specs_path.read_text(encoding="utf-8"))
    task_id = str(task["id"])
    spec = _object(specs.get(task_id), f"candidate spec for {task_id}")
    candidate_type = str(spec["type"])
    edits: list[dict[str, Any]] = []
    if candidate_type in {"remove_bounded_block", "remove_markdown_sections"}:
        relative = Path(str(spec["path"]))
        path = workspace / relative
        before = path.read_text(encoding="utf-8")
        if candidate_type == "remove_bounded_block":
            after = remove_bounded_block(
                before,
                start=str(spec["start"]),
                end=str(spec["end"]),
            )
        else:
            after = remove_markdown_sections(
                before,
                tuple(
                    str(item) for item in _sequence(spec.get("headings"), "headings")
                ),
            )
        path.write_text(after, encoding="utf-8", newline="\n")
        edits.append(_edit_record(relative.as_posix(), before, after, candidate_type))
    elif candidate_type == "contextlens_conservative":
        scan = scan_repository(workspace)
        selected_paths = tuple(
            str(item)
            for item in _sequence(spec.get("selected_paths"), "selected_paths")
        )
        candidate = build_minimization_candidate(
            scan,
            selected_paths=selected_paths,
        )
        before_by_path = {source.path: source.content for source in scan.sources}
        after_by_path = {source.path: source.content for source in candidate.sources}
        edited_paths = set(selected_paths)
        edited_paths.update(
            edit.replacement_path
            for edit in candidate.edits
            if edit.replacement_path is not None
        )
        for relative_path in sorted(edited_paths):
            before = before_by_path.get(relative_path, "")
            after = after_by_path.get(relative_path, "")
            if before == after:
                continue
            path = workspace / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(after, encoding="utf-8", newline="\n")
            edits.append(_edit_record(relative_path, before, after, candidate_type))
    else:
        raise ValueError(f"unknown candidate type: {candidate_type}")

    patch = "".join(str(item["patch"]) for item in edits)
    return {
        "study_id": study["id"],
        "task_id": task_id,
        "strategy": candidate_type,
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate_hash": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        "edits": edits,
        "patch": patch,
        "no_candidate": not edits,
    }


def ensure_mirror(study: Mapping[str, Any], cache: Path) -> Path:
    mirror = cache / "mirrors" / f"{study['id']}.git"
    if not mirror.exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        _run(
            (
                "git",
                "clone",
                "--mirror",
                "--filter=blob:none",
                str(study["clone_url"]),
                str(mirror),
            )
        )
    return mirror


def fetch_task_commits(mirror: Path, task: Mapping[str, Any]) -> None:
    _run(
        (
            "git",
            "--git-dir",
            str(mirror),
            "fetch",
            "--filter=blob:none",
            "origin",
            str(task["pre_fix_commit"]),
            str(task["fixed_commit"]),
        )
    )


def checkout_commit(mirror: Path, commit: str, cache: Path, label: str) -> Path:
    root = cache / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / f"{label}-{uuid.uuid4().hex[:8]}"
    _run(
        (
            "git",
            "--git-dir",
            str(mirror),
            "-c",
            "core.longpaths=true",
            "-c",
            "core.autocrlf=false",
            "worktree",
            "add",
            "--detach",
            str(workspace),
            commit,
        )
    )
    return workspace


def extract_hidden_grader(
    mirror: Path,
    task: Mapping[str, Any],
    cache: Path,
) -> Path:
    grader = _object(task.get("hidden_grader"), "hidden_grader")
    destination = (
        cache / "graders" / str(task["id"]) / Path(str(grader["source_path"])).name
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if grader["source"] == "upstream_fixed_commit":
        content = _run_bytes(
            (
                "git",
                "--git-dir",
                str(mirror),
                "show",
                f"{task['fixed_commit']}:{grader['source_path']}",
            )
        )
        destination.write_bytes(content)
    elif grader["source"] == "case_study_owned_behavioral_test":
        source = HERE.parent / Path(str(grader["source_path"]))
        shutil.copy2(source, destination)
    else:
        raise ValueError(f"unknown hidden grader source: {grader['source']}")
    return destination


def prepare_task(
    study: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    cache: Path,
    use_locked_candidate: bool = False,
) -> dict[str, Any]:
    mirror = ensure_mirror(study, cache)
    fetch_task_commits(mirror, task)
    workspace = checkout_commit(
        mirror,
        str(task["pre_fix_commit"]),
        cache,
        f"run-{study['id']}",
    )
    grader = extract_hidden_grader(mirror, task, cache)
    output_directory = HERE / str(study["id"]) / "candidates"
    output_directory.mkdir(parents=True, exist_ok=True)
    patch_path = output_directory / f"{task['id']}.diff"
    metadata_path = output_directory / f"{task['id']}.json"
    if use_locked_candidate:
        metadata = _apply_locked_candidate(
            workspace,
            patch_path=patch_path,
            metadata_path=metadata_path,
        )
    else:
        candidate = prepare_candidate(workspace, study, task)
        patch_path.write_text(
            str(candidate["patch"]),
            encoding="utf-8",
            newline="\n",
        )
        metadata = {key: value for key, value in candidate.items() if key != "patch"}
        metadata.update(
            {
                "repository": study["repository"],
                "pre_fix_commit": task["pre_fix_commit"],
                "fixed_commit": task["fixed_commit"],
                "hidden_grader_sha256": _sha256(grader),
                "patch_path": str(patch_path.relative_to(HERE)),
            }
        )
        _write_json(metadata_path, metadata)
    return {
        "workspace": str(workspace),
        "grader": str(grader),
        "candidate": metadata,
    }


def _apply_locked_candidate(
    workspace: Path,
    *,
    patch_path: Path,
    metadata_path: Path,
) -> dict[str, Any]:
    if not patch_path.is_file() or not metadata_path.is_file():
        raise FileNotFoundError(
            "locked candidate artifacts are missing; run the prepare command first"
        )
    patch = patch_path.read_text(encoding="utf-8")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("locked candidate metadata must be a JSON object")
    digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    if metadata.get("candidate_hash") != digest:
        raise ValueError("locked candidate patch hash does not match metadata")
    _run(
        ("git", "apply", "--check", "--whitespace=nowarn", str(patch_path)),
        cwd=workspace,
    )
    _run(("git", "apply", "--whitespace=nowarn", str(patch_path)), cwd=workspace)
    return metadata


def validate_task(
    study: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    cache: Path,
    install: bool,
) -> dict[str, Any]:
    mirror = ensure_mirror(study, cache)
    fetch_task_commits(mirror, task)
    grader = extract_hidden_grader(mirror, task, cache)
    results: dict[str, Any] = {}
    for variant, revision in (
        ("buggy", str(task["pre_fix_commit"])),
        ("upstream_fixed", str(task["fixed_commit"])),
    ):
        workspace = checkout_commit(
            mirror,
            revision,
            cache,
            f"v-{study['id']}-{variant[0]}",
        )
        try:
            environment = _prepare_case_environment(str(study["id"]), workspace)
            setup = (
                _setup_commands(str(study["id"]), str(task["id"])) if install else None
            )
            setup_result = _run_setup(setup, workspace) if setup else None
            grader_result = _run_hidden_grader(workspace, task, grader)
            results[variant] = {
                "revision": revision,
                "workspace": str(workspace),
                "workspace_retained": False,
                "environment": environment,
                "setup": setup_result,
                "grader": grader_result,
            }
        finally:
            cleanup_warning = _cleanup_worktree(mirror, workspace)
            if variant in results:
                results[variant]["cleanup_warning"] = cleanup_warning
    setup_valid = all(
        value["setup"] is None or value["setup"]["exit_code"] == 0
        for value in results.values()
    )
    grader_valid = (
        results["buggy"]["grader"]["exit_code"] != 0
        and results["upstream_fixed"]["grader"]["exit_code"] == 0
    )
    valid = setup_valid and grader_valid
    test_discovery_blocked = all(
        "No test files found"
        in (str(value["grader"]["stdout"]) + "\n" + str(value["grader"]["stderr"]))
        for value in results.values()
    )
    status = (
        "ready_for_agent_run"
        if valid
        else "dependency_setup_failed"
        if not setup_valid
        else "infrastructure_blocked_test_discovery"
        if test_discovery_blocked
        else "grader_validation_failed"
    )
    report = {
        "schema_version": "1.0",
        "study_id": study["id"],
        "task_id": task["id"],
        "validated_at": datetime.now(UTC).isoformat(),
        "hidden_grader_sha256": _sha256(grader),
        "setup_valid": setup_valid,
        "grader_valid": grader_valid,
        "infrastructure_blocked": test_discovery_blocked,
        "valid": valid,
        "status": status,
        "variants": results,
    }
    output = HERE / str(study["id"]) / "results" / f"{task['id']}-validation.json"
    _write_json(output, report)
    return report


def run_agent_trials(
    study: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    cache: Path,
    trials: int,
    model: str,
    codex_command: str | None,
    install: bool,
) -> dict[str, Any]:
    if trials not in {1, 3}:
        raise ValueError(
            "external studies permit only one smoke trial or three full trials"
        )
    _require_validated_task(study, task)
    try:
        prepared = prepare_task(
            study,
            task,
            cache=cache,
            use_locked_candidate=True,
        )
    except Exception as exception:
        return _persist_pre_agent_failure(
            study,
            task,
            trials=trials,
            stage="repository_preparation",
            exception=exception,
        )
    workspace = Path(str(prepared["workspace"]))
    grader = Path(str(prepared["grader"]))
    environment_metadata = (
        _prepare_case_environment(str(study["id"]), workspace) if install else None
    )
    setup_commands = (
        _setup_commands(str(study["id"]), str(task["id"])) if install else ()
    )
    grader_value = _object(task.get("hidden_grader"), "hidden_grader")
    grade_command = [
        sys.executable,
        str(HERE / "grade.py"),
        "--fixture",
        str(grader),
        "--destination",
        str(grader_value["destination_path"]),
        "--working-directory",
        str(grader_value.get("working_directory", ".")),
        *(["--replace-existing"] if grader_value.get("replace_existing") else []),
        "--",
        *(
            str(item)
            for item in _sequence(grader_value.get("command"), "grader command")
        ),
    ]
    agent_config: dict[str, Any] = {
        "type": "codex",
        "provider": "openai",
        "model": model,
        "reasoning_effort": "low",
        "sandbox": "workspace-write",
        "tools": ["shell"],
    }
    if codex_command is not None:
        agent_config["command"] = [codex_command]
    config = {
        "trials": trials,
        "max_runs": trials * 2,
        "context_provider": study["context_provider"],
        "quality_tolerance": 0,
        "economics_tolerance": 0.02,
        "require_provider_usage": True,
        "agent": agent_config,
        "tasks": [
            {
                "id": task["id"],
                "instruction": task["task_prompt"],
                "workspace": ".",
                "checks": [grade_command],
                "setup": [
                    {
                        "command": list(command),
                        "working_directory": relative.as_posix(),
                    }
                    for command, relative in setup_commands
                ],
                "target_paths": task["target_paths"],
                "context_provider": study["context_provider"],
                "timeout_seconds": 900,
                "category": "historical_bug_fix",
                "repository_scope": study["repository"],
                "snapshot_identity": (
                    f"{task['pre_fix_commit']}:{environment_metadata['sha256']}"
                    if environment_metadata is not None
                    else str(task["pre_fix_commit"])
                ),
            }
        ],
    }
    with tempfile.TemporaryDirectory(prefix="contextlens-study-config-") as directory:
        config_path = Path(directory) / "evals.json"
        _write_json(config_path, config)
        verification = verify_repository(
            config_path,
            root=workspace,
            base_ref=str(task["pre_fix_commit"]),
            progress=_print_experiment_event,
        )
    report = verification.to_dict()
    report["case_study"] = {
        "study_id": study["id"],
        "task_id": task["id"],
        "repository": study["repository"],
        "pre_fix_commit": task["pre_fix_commit"],
        "fixed_commit": task["fixed_commit"],
        "candidate": prepared["candidate"],
        "codex_command": codex_command,
        "model": model,
        "reasoning_effort": "low",
        "platform": sys.platform,
        "python": sys.version,
    }
    output = _case_result_path(study, task, trials)
    _write_json(output, report)
    output.with_suffix(".md").write_text(
        render_verification_markdown(verification),
        encoding="utf-8",
        newline="\n",
    )
    return report


def _case_result_path(
    study: Mapping[str, Any],
    task: Mapping[str, Any],
    trials: int,
) -> Path:
    return (
        HERE
        / str(study["id"])
        / "results"
        / (f"{task['id']}-{'smoke' if trials == 1 else 'full'}.json")
    )


def _persist_pre_agent_failure(
    study: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    trials: int,
    stage: str,
    exception: Exception,
) -> dict[str, Any]:
    report = {
        "schema_version": "1.0",
        "report_type": "context_regression_verification",
        "verdict": "INCONCLUSIVE",
        "rationale": f"Infrastructure failed during {stage} before agent execution.",
        "agent_executions": 0,
        "infrastructure_errors": [
            {
                "stage": stage,
                "classification": "infrastructure_error",
                "error": f"{type(exception).__name__}: {exception}",
            }
        ],
        "case_study": {
            "study_id": study["id"],
            "task_id": task["id"],
            "repository": study["repository"],
            "pre_fix_commit": task["pre_fix_commit"],
            "fixed_commit": task["fixed_commit"],
        },
    }
    output = _case_result_path(study, task, trials)
    _write_json(output, report)
    output.with_suffix(".md").write_text(
        (
            f"# {study['id']} — {task['id']}\n\n"
            f"Infrastructure failed during `{stage}` before agent execution.\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    return report


def _require_validated_task(
    study: Mapping[str, Any],
    task: Mapping[str, Any],
) -> None:
    path = HERE / str(study["id"]) / "results" / f"{task['id']}-validation.json"
    if not path.is_file():
        raise RuntimeError(
            "benchmark grader has not been validated; run "
            f"python case-studies/run.py validate {study['id']} {task['id']} "
            "--install"
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("valid") is not True:
        status = (
            value.get("status", "invalid") if isinstance(value, dict) else "invalid"
        )
        raise RuntimeError(
            f"benchmark grader is not valid ({status}); refusing agent execution"
        )


def _print_experiment_event(event: ExperimentEvent) -> None:
    label = event.variant.upper()
    if event.phase == "starting":
        if event.order_position == 1:
            print(f"\nTrial {event.trial}")
        print(f"  {label:<10} starting...", flush=True)
        return
    result = event.result
    assert result is not None
    outcome = result.outcome
    input_tokens = outcome.input_tokens if outcome is not None else None
    usage = (
        f"{input_tokens:,} input tokens"
        if input_tokens is not None
        else "input unavailable"
    )
    classification = event.classification.value if event.classification else "unknown"
    print(
        f"  {label:<10} {classification.upper():<20} "
        f"{result.duration_seconds:.1f}s  {usage}",
        flush=True,
    )


def _run_hidden_grader(
    workspace: Path,
    task: Mapping[str, Any],
    fixture: Path,
) -> dict[str, Any]:
    grader = _object(task.get("hidden_grader"), "hidden_grader")
    destination = workspace / str(grader["destination_path"])
    original = destination.read_bytes() if destination.exists() else None
    if original is not None and not grader.get("replace_existing"):
        raise FileExistsError(
            f"hidden grader destination already exists: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fixture, destination)
    try:
        working = workspace / str(grader.get("working_directory", "."))
        return _run_captured(
            _normalize_path_arguments(
                tuple(
                    str(item)
                    for item in _sequence(grader.get("command"), "grader command")
                ),
                working,
            ),
            cwd=working,
        )
    finally:
        if original is None:
            destination.unlink(missing_ok=True)
        else:
            destination.write_bytes(original)


def _setup_commands(
    study_id: str, task_id: str
) -> tuple[tuple[tuple[str, ...], Path], ...]:
    if study_id == "browser-use":
        return ((("uv", "sync", "--all-extras", "--dev"), Path(".")),)
    if study_id == "infisical":
        package = "backend" if "backend" in task_id else "frontend"
        return ((("npm", "ci"), Path(package)),)
    if study_id == "langfuse":
        return (
            (
                (
                    "pnpm",
                    "install",
                    "--frozen-lockfile",
                    "--ignore-scripts",
                ),
                Path("."),
            ),
            (
                (
                    "pnpm",
                    "--filter",
                    "@langfuse/shared",
                    "run",
                    "db:generate",
                ),
                Path("."),
            ),
        )
    raise ValueError(f"no setup command for study {study_id!r}")


def _prepare_case_environment(study_id: str, workspace: Path) -> dict[str, str] | None:
    if study_id != "langfuse":
        return None
    source = workspace / ".env.dev.example"
    destination = workspace / ".env"
    shutil.copy2(source, destination)
    return {
        "source": source.name,
        "destination": destination.name,
        "sha256": _sha256(destination),
    }


def _run_setup(
    steps: tuple[tuple[tuple[str, ...], Path], ...], workspace: Path
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for command, relative in steps:
        result = _run_captured(command, cwd=workspace / relative)
        results.append(result)
        if result["exit_code"] != 0:
            break
    return {
        "exit_code": next(
            (result["exit_code"] for result in results if result["exit_code"] != 0),
            0,
        ),
        "steps": results,
        "stdout": "\n".join(str(result["stdout"]) for result in results),
        "stderr": "\n".join(str(result["stderr"]) for result in results),
    }


def _edit_record(path: str, before: str, after: str, operation: str) -> dict[str, Any]:
    patch = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return {
        "path": path,
        "operation": operation,
        "before_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
        "after_sha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
        "before_estimated_tokens": (len(before.encode("utf-8")) + 3) // 4,
        "after_estimated_tokens": (len(after.encode("utf-8")) + 3) // 4,
        "patch": patch,
    }


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if command and command[0] == "git":
        environment["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
        text=True,
        timeout=600,
    )


def _cleanup_worktree(mirror: Path, workspace: Path) -> str | None:
    subprocess.run(
        (
            "git",
            "-c",
            "core.longpaths=true",
            "--git-dir",
            str(mirror),
            "worktree",
            "remove",
            "--force",
            str(workspace),
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    cleanup_path = workspace
    if workspace.exists():
        shortened = workspace.parent / f"cleanup-{uuid.uuid4().hex[:8]}"
        try:
            workspace.rename(shortened)
            cleanup_path = shortened
        except OSError:
            pass
    cleanup_error: OSError | None = None
    for _ in range(3):
        if not cleanup_path.exists():
            break
        try:
            shutil.rmtree(cleanup_path, onerror=_remove_readonly)
        except OSError as error:
            cleanup_error = error
            time.sleep(0.25)
    subprocess.run(
        ("git", "--git-dir", str(mirror), "worktree", "prune"),
        check=False,
        capture_output=True,
        text=True,
    )
    if cleanup_path.exists():
        return str(cleanup_error or "workspace cleanup incomplete")
    return None


def _remove_readonly(function: Any, path: str, _: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _run_bytes(command: Sequence[str]) -> bytes:
    return subprocess.run(command, check=True, capture_output=True).stdout


def _run_captured(command: Sequence[str], *, cwd: Path) -> dict[str, Any]:
    started = datetime.now(UTC)
    resolved_command = _resolve_command(command)
    completed = subprocess.run(
        resolved_command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    ended = datetime.now(UTC)
    return {
        "command": list(command),
        "resolved_executable": resolved_command[0],
        "working_directory": str(cwd),
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": (ended - started).total_seconds(),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _resolve_command(command: Sequence[str]) -> tuple[str, ...]:
    if not command:
        raise ValueError("command cannot be empty")
    executable = shutil.which(str(command[0]))
    if executable is None:
        return tuple(str(item) for item in command)
    return (executable, *(str(item) for item in command[1:]))


def _normalize_path_arguments(
    command: Sequence[str], working_directory: Path
) -> tuple[str, ...]:
    if not command:
        raise ValueError("command cannot be empty")
    normalized = [str(command[0])]
    for item in command[1:]:
        argument = str(item)
        path = Path(argument)
        candidate = path if path.is_absolute() else working_directory / path
        normalized.append(str(path) if candidate.exists() else argument)
    return tuple(normalized)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    for name in ("prepare", "validate", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("study")
        command.add_argument("task")
        if name in {"validate", "run"}:
            command.add_argument("--install", action="store_true")
        if name == "run":
            command.add_argument("--trials", type=int, choices=(1, 3), default=1)
            command.add_argument("--model", default="gpt-5.6-terra")
            command.add_argument(
                "--codex-command",
                help=(
                    "Codex executable path; omit to use the pinned npx-based "
                    "adapter command"
                ),
            )
    args = parser.parse_args()
    if args.command == "list":
        for study in load_agent_studies():
            for task in _sequence(study["tasks"], "tasks"):
                print(f"{study['id']}/{task['id']}\t{task['status']}")
        return 0
    study, task = selected_task(args.study, args.task)
    if args.command == "prepare":
        result = prepare_task(study, task, cache=args.cache)
    elif args.command == "validate":
        result = validate_task(study, task, cache=args.cache, install=args.install)
    else:
        result = run_agent_trials(
            study,
            task,
            cache=args.cache,
            trials=args.trials,
            model=args.model,
            codex_command=args.codex_command,
            install=args.install,
        )
    print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
