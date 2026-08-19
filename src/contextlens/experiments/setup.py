"""Deterministic pre-agent workspace setup for isolated replays."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from contextlens.experiments.model import ReplayTask


@dataclass(frozen=True, slots=True)
class WorkspaceSetupCommand:
    """One shell-free command executed before the agent can inspect a workspace."""

    command: tuple[str, ...]
    working_directory: str = "."

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", tuple(self.command))
        if not self.command or any(not part for part in self.command):
            raise ValueError("workspace setup command cannot be empty")
        relative = Path(self.working_directory.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("workspace setup directory must stay within the workspace")

    def to_dict(self) -> dict[str, object]:
        return {
            "command": list(self.command),
            "working_directory": self.working_directory,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceSetupResult:
    """Complete retained output from pre-agent setup."""

    passed: bool
    commands: tuple[dict[str, object], ...]
    duration_seconds: float
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "commands": [dict(command) for command in self.commands],
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


class WorkspacePreparer(Protocol):
    """Prepare one fresh workspace before its agent process starts."""

    @property
    def preparer_id(self) -> str:
        """Stable setup identity for fixed-dimension hashing."""

    @property
    def definition(self) -> tuple[dict[str, object], ...]:
        """Serializable setup definition."""

    def prepare(self, workspace: Path, task: ReplayTask) -> WorkspaceSetupResult:
        """Run setup and retain success or failure evidence."""


class CommandWorkspacePreparer:
    """Run ordered setup commands without invoking an intermediate shell."""

    def __init__(
        self,
        commands: tuple[WorkspaceSetupCommand, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        if not commands:
            raise ValueError("workspace preparer requires at least one command")
        if timeout_seconds <= 0:
            raise ValueError("workspace setup timeout must be positive")
        self.commands = tuple(commands)
        self.timeout_seconds = timeout_seconds

    @property
    def preparer_id(self) -> str:
        return "command-workspace-preparer-v1"

    @property
    def definition(self) -> tuple[dict[str, object], ...]:
        return tuple(command.to_dict() for command in self.commands)

    def prepare(self, workspace: Path, task: ReplayTask) -> WorkspaceSetupResult:
        del task
        started = time.monotonic()
        records: list[dict[str, object]] = []
        for setup in self.commands:
            remaining = self.timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                return WorkspaceSetupResult(
                    passed=False,
                    commands=tuple(records),
                    duration_seconds=time.monotonic() - started,
                    error="workspace setup timed out",
                )
            working = (workspace / setup.working_directory).resolve()
            try:
                working.relative_to(workspace.resolve())
            except ValueError as error:
                raise ValueError("workspace setup escaped the workspace") from error
            try:
                executable = shutil.which(setup.command[0])
                command = (
                    (executable, *setup.command[1:])
                    if executable is not None
                    else setup.command
                )
                completed = subprocess.run(
                    command,
                    cwd=working,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=remaining,
                    check=False,
                )
            except OSError as error:
                records.append(
                    {
                        **setup.to_dict(),
                        "exit_code": None,
                        "timed_out": False,
                        "stdout": "",
                        "stderr": f"{type(error).__name__}: {error}",
                    }
                )
                return WorkspaceSetupResult(
                    passed=False,
                    commands=tuple(records),
                    duration_seconds=time.monotonic() - started,
                    error=f"workspace setup could not start: {error}",
                )
            except subprocess.TimeoutExpired as error:
                records.append(
                    {
                        **setup.to_dict(),
                        "exit_code": None,
                        "timed_out": True,
                        "stdout": _stream(error.stdout),
                        "stderr": _stream(error.stderr),
                    }
                )
                return WorkspaceSetupResult(
                    passed=False,
                    commands=tuple(records),
                    duration_seconds=time.monotonic() - started,
                    error="workspace setup command timed out",
                )
            records.append(
                {
                    **setup.to_dict(),
                    "exit_code": completed.returncode,
                    "timed_out": False,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            )
            if completed.returncode != 0:
                return WorkspaceSetupResult(
                    passed=False,
                    commands=tuple(records),
                    duration_seconds=time.monotonic() - started,
                    error=(
                        "workspace setup command exited with code "
                        f"{completed.returncode}"
                    ),
                )
        return WorkspaceSetupResult(
            passed=True,
            commands=tuple(records),
            duration_seconds=time.monotonic() - started,
        )


def _stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
