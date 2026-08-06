"""Mechanical verification executed before an isolated workspace is removed."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from contextlens.experiments.model import AgentOutcome, ReplayTask


@dataclass(frozen=True, slots=True)
class WorkspaceVerification:
    """Raw evidence from a fixed mechanical check in a replay workspace."""

    command: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.exit_code == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "command": list(self.command),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "timed_out": self.timed_out,
            "passed": self.passed,
        }


class WorkspaceVerifier(Protocol):
    """Verify an agent outcome while its isolated workspace still exists."""

    @property
    def verifier_id(self) -> str:
        """Stable verifier identity."""

    def verify(
        self,
        workspace: Path,
        task: ReplayTask,
        outcome: AgentOutcome,
    ) -> WorkspaceVerification:
        """Run a mechanical check and return complete evidence."""


class CommandWorkspaceVerifier:
    """Run a fixed command with the isolated workspace as its working directory."""

    def __init__(
        self,
        command: tuple[str, ...],
        *,
        timeout_seconds: float = 120.0,
        verifier_id: str = "command-workspace-verifier-v1",
        environment_allowlist: tuple[str, ...] = (
            "PATH",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "PATHEXT",
            "COMSPEC",
            "WINDIR",
        ),
    ) -> None:
        if not command:
            raise ValueError("verification command cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("verification timeout must be positive")
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds
        self._verifier_id = verifier_id
        self.environment_allowlist = tuple(environment_allowlist)

    @property
    def verifier_id(self) -> str:
        return self._verifier_id

    def verify(
        self,
        workspace: Path,
        task: ReplayTask,
        outcome: AgentOutcome,
    ) -> WorkspaceVerification:
        del task, outcome
        environment = {
            name: value
            for name, value in os.environ.items()
            if name in self.environment_allowlist
        }
        started = time.monotonic()
        try:
            completed = subprocess.run(
                self.command,
                cwd=workspace,
                env=environment,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            return WorkspaceVerification(
                command=self.command,
                exit_code=None,
                stdout=_text(error.stdout),
                stderr=_text(error.stderr),
                duration_seconds=time.monotonic() - started,
                timed_out=True,
            )
        return WorkspaceVerification(
            command=self.command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=time.monotonic() - started,
        )


def _text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
