"""Frozen real-repository tasks and their hidden mechanical verification.

A manifest pins a public GitHub repository at one commit, the original issue
text the agent is allowed to see, and verification the agent never sees. The
gold patch and the hidden assertions stay on the host.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "coding"
_SHA = re.compile(r"[0-9a-f]{40}")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


@dataclass(frozen=True, slots=True)
class Task:
    """One frozen task: public issue text, hidden checks."""

    path: Path
    case_id: str
    repo: str
    commit: str
    task: str
    setup: tuple[tuple[str, ...], ...]
    verification: tuple[tuple[str, ...], ...]
    verification_patch: str | None

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.repo}.git"

    @property
    def gold_patch(self) -> Path:
        return self.path.with_suffix(".patch")

    def public_value(self) -> dict[str, Any]:
        """The manifest without hidden assertions or the gold patch."""

        return {
            "case_id": self.case_id,
            "repo": self.repo,
            "commit": self.commit,
            "task": self.task,
            "setup": [list(command) for command in self.setup],
            "verification_commands": len(self.verification),
            "manifest_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
        }


def load_task(path: Path) -> Task:
    path = path.resolve()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("task manifest must be an object")
    repo = _required(value, "repo")
    commit = _required(value, "commit").lower()
    if not _REPO.fullmatch(repo):
        raise ValueError(f"invalid public GitHub repository: {repo!r}")
    if not _SHA.fullmatch(commit):
        raise ValueError("commit must be a full 40-character SHA")
    verification = value.get("verification")
    if not isinstance(verification, dict):
        raise ValueError("verification must be an object")
    patch: str | None = None
    patch_name = verification.get("patch_file")
    if patch_name is not None:
        if not isinstance(patch_name, str) or not patch_name:
            raise ValueError("verification.patch_file must be a nonempty string")
        patch_path = (path.parent / patch_name).resolve()
        if path.parent not in patch_path.parents:
            raise ValueError("verification patch must stay below the case directory")
        patch = patch_path.read_text(encoding="utf-8")
    return Task(
        path=path,
        case_id=_required(value, "case_id"),
        repo=repo,
        commit=commit,
        task=_required(value, "task"),
        setup=_commands(value.get("setup", []), "setup"),
        verification=_commands(verification.get("commands"), "verification.commands"),
        verification_patch=patch,
    )


def load_tasks(directory: Path = FIXTURES) -> tuple[Task, ...]:
    return tuple(
        load_task(path)
        for path in sorted(directory.glob("*.json"))
        if path.name != "selection.json"
    )


def acquire_repository(task: Task, destination: Path) -> Path:
    """Fetch only the pinned commit and remove access to upstream history."""

    destination = destination.resolve()
    if destination.exists():
        raise ValueError(f"checkout destination already exists: {destination}")
    destination.mkdir(parents=True)
    _git(("init", "--quiet"), destination)
    _git(("remote", "add", "origin", task.clone_url), destination)
    try:
        _git(("fetch", "--quiet", "--depth=1", "origin", task.commit), destination)
        _git(("checkout", "--quiet", "--detach", "FETCH_HEAD"), destination)
    finally:
        _git(("remote", "remove", "origin"), destination, check=False)
    actual = _git(("rev-parse", "HEAD"), destination).stdout.strip().lower()
    if actual != task.commit:
        raise RuntimeError(f"checkout resolved to {actual}, expected {task.commit}")
    if _git(("rev-list", "--all", "--count"), destination).stdout.strip() != "1":
        raise RuntimeError("isolated checkout unexpectedly contains history")
    return destination


def verify(
    workspace: Path, commands: tuple[tuple[str, ...], ...], timeout: int = 180
) -> dict[str, Any]:
    """Run the hidden checks. ``returncode: None`` means infrastructure failed."""

    results: list[dict[str, Any]] = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            results.append(
                {
                    "command": list(command),
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-8000:],
                    "stderr": completed.stderr[-8000:],
                }
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            results.append(
                {"command": list(command), "returncode": None, "error": str(error)}
            )
    return {
        "success": bool(results)
        and all(item["returncode"] == 0 for item in results),
        "commands": results,
    }


def head_commit(workspace: Path) -> str:
    return _git(("rev-parse", "HEAD"), workspace).stdout.strip()


def working_patch(workspace: Path) -> bytes:
    """The agent's diff, including files it created."""

    _git(("add", "-N", "."), workspace)
    completed = subprocess.run(
        ("git", "diff", "--binary", "HEAD"),
        cwd=workspace,
        capture_output=True,
        timeout=120,
        check=True,
    )
    return completed.stdout


def apply_patch(workspace: Path, diff: bytes) -> str | None:
    """Apply a patch, returning stderr when it does not apply."""

    if not diff:
        return None
    completed = subprocess.run(
        ("git", "apply", "--binary", "-"),
        input=diff,
        cwd=workspace,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode:
        return completed.stderr.decode(errors="replace")
    return None


def _required(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a nonempty string")
    return item.strip()


def _commands(value: Any, name: str) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list of argument lists")
    commands: list[tuple[str, ...]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or not item
            or not all(isinstance(part, str) and part for part in item)
        ):
            raise ValueError(f"{name} entries must be nonempty string lists")
        commands.append(tuple(item))
    return tuple(commands)


def _git(
    arguments: tuple[str, ...], cwd: Path, *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return completed
