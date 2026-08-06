"""Hidden executable verification for an evaluation case workspace."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Any

from contextlens.experiments import AgentOutcome, ReplayTask
from contextlens.experiments.verification import WorkspaceVerification
from evals.cases import EvalCase, JsonExpectation


class HiddenCaseVerifier:
    """Verify fixed hidden expectations without exposing them to the agent."""

    def __init__(
        self,
        case: EvalCase,
        *,
        timeout_seconds: float = 30.0,
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
        if timeout_seconds <= 0:
            raise ValueError("verification timeout must be positive")
        self.case = case
        self.timeout_seconds = timeout_seconds
        self.environment_allowlist = tuple(environment_allowlist)

    @property
    def verifier_id(self) -> str:
        return f"hidden-case-verifier-v1:{self.case.case_id}"

    def verify(
        self,
        workspace: Path,
        task: ReplayTask,
        outcome: AgentOutcome,
    ) -> WorkspaceVerification:
        del outcome
        started = time.monotonic()
        failures: list[str] = []
        evidence: list[str] = []
        if task.task_id != self.case.case_id:
            failures.append(
                f"task ID {task.task_id!r} does not match case {self.case.case_id!r}"
            )
        spec = self.case.verification
        for relative in spec.required_files:
            if not _path(workspace, relative).is_file():
                failures.append(f"required file is missing: {relative}")
        for relative in spec.forbidden_files:
            if _path(workspace, relative).exists():
                failures.append(f"forbidden path exists: {relative}")
        for relative, expected in spec.exact_files.items():
            path = _path(workspace, relative)
            if not path.is_file():
                failures.append(f"exact-match file is missing: {relative}")
                continue
            actual = path.read_text(encoding="utf-8")
            if actual.strip() != expected.strip():
                failures.append(f"file content did not match: {relative}")
        for relative, fragments in spec.contains.items():
            path = _path(workspace, relative)
            if not path.is_file():
                failures.append(f"content-check file is missing: {relative}")
                continue
            actual = path.read_text(encoding="utf-8")
            for fragment in fragments:
                if fragment not in actual:
                    failures.append(f"file {relative} did not contain {fragment!r}")
        documents: dict[str, Any] = {}
        for expectation in spec.json_expectations:
            self._check_json(workspace, expectation, documents, failures)
        environment = {
            name: value
            for name, value in os.environ.items()
            if name in self.environment_allowlist
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        timed_out = False
        if spec.patch:
            applied = subprocess.run(
                ("git", "apply", "--whitespace=nowarn", "-"),
                cwd=workspace,
                env=environment,
                input=spec.patch,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            evidence.extend((applied.stdout, applied.stderr))
            if applied.returncode != 0:
                evidence.append(
                    "hidden test patch did not apply; running the independent "
                    "semantic check against the candidate workspace"
                )
        for check in spec.commands:
            try:
                completed = subprocess.run(
                    check.command,
                    cwd=workspace,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                timed_out = True
                failures.append(f"hidden command timed out: {check.command!r}")
                evidence.extend(
                    (
                        _text(error.stdout),
                        _text(error.stderr),
                    )
                )
                continue
            evidence.extend((completed.stdout, completed.stderr))
            if completed.returncode != check.expected_exit_code:
                failures.append(
                    "hidden command returned "
                    f"{completed.returncode}, expected {check.expected_exit_code}: "
                    f"{check.command!r}"
                )
        return WorkspaceVerification(
            command=("contextlens-hidden-verify", self.case.case_id),
            exit_code=0 if not failures and not timed_out else 1,
            stdout="\n".join(item for item in evidence if item),
            stderr="\n".join(failures),
            duration_seconds=time.monotonic() - started,
            timed_out=timed_out,
        )

    @staticmethod
    def _check_json(
        workspace: Path,
        expectation: JsonExpectation,
        documents: dict[str, Any],
        failures: list[str],
    ) -> None:
        if expectation.path not in documents:
            path = _path(workspace, expectation.path)
            try:
                documents[expectation.path] = json.loads(
                    path.read_text(encoding="utf-8")
                )
            except (FileNotFoundError, json.JSONDecodeError) as error:
                failures.append(f"invalid JSON {expectation.path}: {error}")
                documents[expectation.path] = None
        current = documents[expectation.path]
        try:
            for key in expectation.key_path:
                current = current[key]
        except (KeyError, IndexError, TypeError) as error:
            failures.append(
                f"missing JSON path {expectation.key_path!r} in "
                f"{expectation.path}: {error}"
            )
            return
        if current != expectation.expected:
            failures.append(
                f"JSON {expectation.path} path {expectation.key_path!r} "
                f"was {current!r}, expected {expectation.expected!r}"
            )


def _path(workspace: Path, relative: str) -> Path:
    return workspace.joinpath(*PurePosixPath(relative).parts)


def _text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    return (
        value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    )
