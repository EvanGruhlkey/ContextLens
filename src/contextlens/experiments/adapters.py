"""Agent replay adapter contracts and subprocess integration."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Protocol

from contextlens.experiments.model import AgentOutcome, ReplayRequest


class AgentAdapter(Protocol):
    """Run an agent using exactly the supplied request."""

    @property
    def adapter_id(self) -> str:
        """Stable adapter identity used for reproducibility and caching."""

    def run(self, request: ReplayRequest) -> AgentOutcome:
        """Execute one replay or raise an exception."""


class SubprocessAgentAdapter:
    """Invoke an external agent that consumes a JSON replay request."""

    def __init__(
        self,
        command: tuple[str, ...],
        *,
        adapter_id: str = "subprocess-v1",
    ) -> None:
        if not command:
            raise ValueError("command cannot be empty")
        self.command = command
        self._adapter_id = adapter_id

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    def run(self, request: ReplayRequest) -> AgentOutcome:
        workspace = Path(request.workspace)
        request_path = workspace.parent / f"{request.run_id}.request.json"
        request_path.write_text(
            json.dumps(_request_dict(request), ensure_ascii=False),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["CONTEXTLENS_REQUEST"] = str(request_path)
        try:
            completed = subprocess.run(
                self.command,
                cwd=workspace,
                env=environment,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(
                f"agent exceeded {request.timeout_seconds:g} seconds"
            ) from error
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            raise RuntimeError(
                f"agent exited with code {completed.returncode}: {stderr}"
            )
        return AgentOutcome(
            output_text=completed.stdout,
            commands=(" ".join(self.command),),
            metadata={"stderr": completed.stderr, "returncode": completed.returncode},
        )


def _request_dict(request: ReplayRequest) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "run_id": request.run_id,
        "task": {
            "task_id": request.task.task_id,
            "instruction": request.task.instruction,
            "metadata": dict(request.task.metadata),
        },
        "variant": {
            "variant_id": request.variant.variant_id,
            "removed_source_ids": sorted(request.variant.removed_source_ids),
            "description": request.variant.description,
        },
        "context": [source.to_dict() for source in request.context],
        "settings": {
            "provider": request.settings.provider,
            "model": request.settings.model,
            "seed": request.settings.seed,
            "temperature": request.settings.temperature,
            "tools": list(request.settings.tools),
            "parameters": dict(request.settings.parameters),
        },
        "workspace": request.workspace,
        "timeout_seconds": request.timeout_seconds,
    }

