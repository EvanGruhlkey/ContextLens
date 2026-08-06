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
        environment_allowlist: tuple[str, ...] = (
            "PATH",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "PATHEXT",
            "COMSPEC",
            "WINDIR",
        ),
        secret_environment: tuple[str, ...] = (),
    ) -> None:
        if not command:
            raise ValueError("command cannot be empty")
        self.command = command
        self._adapter_id = adapter_id
        self.environment_allowlist = tuple(environment_allowlist)
        self.secret_environment = tuple(secret_environment)

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    def run(self, request: ReplayRequest) -> AgentOutcome:
        workspace = Path(request.workspace)
        request_path = workspace.parent / f"{request.run_id}.request.json"
        result_path = workspace.parent / f"{request.run_id}.result.json"
        request_path.write_text(
            json.dumps(_request_dict(request), ensure_ascii=False),
            encoding="utf-8",
        )
        allowed = {*self.environment_allowlist, *self.secret_environment}
        environment = {
            name: value
            for name, value in os.environ.items()
            if name in allowed
        }
        environment["CONTEXTLENS_REQUEST"] = str(request_path)
        environment["CONTEXTLENS_RESULT"] = str(result_path)
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
        if result_path.exists():
            value = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("CONTEXTLENS_RESULT must contain a JSON object")
        else:
            value = {}
        metadata = dict(value.get("metadata", {}))
        metadata.update(
            {"stderr": completed.stderr, "returncode": completed.returncode}
        )
        return AgentOutcome(
            output_text=str(value.get("output_text", completed.stdout)),
            commands=tuple(
                str(item)
                for item in value.get("commands", (" ".join(self.command),))
            ),
            test_results=tuple(
                str(item)
                for item in value.get("test_results", ())
            ),
            input_tokens=_optional_int(value.get("input_tokens")),
            cached_input_tokens=_optional_int(value.get("cached_input_tokens")),
            output_tokens=_optional_int(value.get("output_tokens")),
            cost_usd=_optional_float(value.get("cost_usd")),
            tool_calls=_optional_int(value.get("tool_calls")) or 0,
            retries=_optional_int(value.get("retries")) or 0,
            lazy_loaded_source_ids=tuple(
                str(item)
                for item in value.get("lazy_loaded_source_ids", ())
            ),
            retrieval_tokens=_optional_int(value.get("retrieval_tokens")) or 0,
            retrieval_latency_ms=(
                _optional_int(value.get("retrieval_latency_ms")) or 0
            ),
            metadata=metadata,
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
            "mutations": [
                mutation.to_dict() for mutation in request.variant.mutations
            ],
        },
        "context": [source.to_dict() for source in request.context],
        "lazy_context": [source.to_dict() for source in request.lazy_context],
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


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("token counts must be integers")
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError("cost must be numeric")
    return float(value)
