"""Fresh-process Codex CLI adapter for production agent replays."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from contextlens.experiments.model import AgentOutcome, ReplayRequest

DEFAULT_CODEX_COMMAND = (
    "npx.cmd" if os.name == "nt" else "npx",
    "-y",
    "@openai/codex@0.146.0",
)
_SANDBOX_MODES = frozenset(
    {"read-only", "workspace-write", "danger-full-access"}
)


class CodexCliExecutionError(RuntimeError):
    """A failed Codex process with its raw invocation evidence attached."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int,
        metadata: Mapping[str, Any],
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.metadata = dict(metadata)


class CodexCliTimeoutError(TimeoutError):
    """A timed-out Codex process with any partial evidence attached."""

    def __init__(self, message: str, *, metadata: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.metadata = dict(metadata)


@dataclass(slots=True)
class _ParsedEvents:
    final_message: str = ""
    thread_id: str | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    commands: list[str] = field(default_factory=list)
    command_events: list[dict[str, Any]] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    all_tool_events: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


class CodexCliAgentAdapter:
    """Run each replay in one new ephemeral ``codex exec`` process.

    The adapter deliberately has no response cache and never reuses a thread.
    The rendered prompt is sent over stdin so large contexts are not constrained
    by platform command-line length limits.
    """

    def __init__(
        self,
        command: Sequence[str] = DEFAULT_CODEX_COMMAND,
        *,
        adapter_id: str = "codex-cli-jsonl-v1",
        environment: Mapping[str, str] | None = None,
        default_reasoning_effort: str = "low",
        default_sandbox: str = "workspace-write",
    ) -> None:
        normalized_command = tuple(command)
        if not normalized_command or any(not part for part in normalized_command):
            raise ValueError("command cannot be empty")
        if not adapter_id:
            raise ValueError("adapter_id cannot be empty")
        _validate_reasoning(default_reasoning_effort)
        _validate_sandbox(default_sandbox)
        self.command = normalized_command
        self._adapter_id = adapter_id
        self.environment = dict(environment or {})
        self.default_reasoning_effort = default_reasoning_effort
        self.default_sandbox = default_sandbox

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    def run(self, request: ReplayRequest) -> AgentOutcome:
        """Execute exactly one request in a fresh Codex CLI process."""

        workspace = Path(request.workspace)
        if not workspace.is_dir():
            raise ValueError(f"workspace is not a directory: {workspace}")
        prompt = render_codex_prompt(request)
        reasoning = _string_parameter(
            request.settings.parameters,
            ("reasoning_effort", "model_reasoning_effort", "reasoning"),
            self.default_reasoning_effort,
        )
        sandbox = _string_parameter(
            request.settings.parameters,
            ("sandbox", "sandbox_mode"),
            self.default_sandbox,
        )
        _validate_reasoning(reasoning)
        _validate_sandbox(sandbox)
        invocation = (
            *self.command,
            "exec",
            "--ephemeral",
            "--json",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--model",
            request.settings.model,
            "--sandbox",
            sandbox,
            *(
                ("-c", 'windows.sandbox="elevated"')
                if os.name == "nt"
                else ()
            ),
            "-c",
            'shell_environment_policy.set.PYTHONDONTWRITEBYTECODE="1"',
            "-c",
            f'model_reasoning_effort="{_toml_escape(reasoning)}"',
            "-",
        )
        environment = os.environ.copy()
        environment.update(self.environment)
        started_at = _timestamp()
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                invocation,
                cwd=workspace,
                env=environment,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=request.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            ended_at = _timestamp()
            latency_ms = round((time.perf_counter() - started) * 1000)
            raw_jsonl = _timeout_stream(error.stdout)
            stderr = _timeout_stream(error.stderr)
            parsed = _parse_jsonl(raw_jsonl)
            parsed.errors.append(
                f"Codex process exceeded {request.timeout_seconds:g} seconds"
            )
            metadata = _metadata(
                request=request,
                invocation=invocation,
                prompt=prompt,
                reasoning=reasoning,
                sandbox=sandbox,
                started_at=started_at,
                ended_at=ended_at,
                latency_ms=latency_ms,
                returncode=None,
                stderr=stderr,
                raw_jsonl=raw_jsonl,
                parsed=parsed,
            )
            raise CodexCliTimeoutError(
                f"Codex agent exceeded {request.timeout_seconds:g} seconds",
                metadata=metadata,
            ) from error

        ended_at = _timestamp()
        latency_ms = round((time.perf_counter() - started) * 1000)
        parsed = _parse_jsonl(completed.stdout)
        if completed.returncode != 0:
            detail = completed.stderr.strip()
            if not detail and parsed.errors:
                detail = parsed.errors[-1]
            parsed.errors.append(
                f"Codex process exited with code {completed.returncode}"
            )
            metadata = _metadata(
                request=request,
                invocation=invocation,
                prompt=prompt,
                reasoning=reasoning,
                sandbox=sandbox,
                started_at=started_at,
                ended_at=ended_at,
                latency_ms=latency_ms,
                returncode=completed.returncode,
                stderr=completed.stderr,
                raw_jsonl=completed.stdout,
                parsed=parsed,
            )
            suffix = f": {detail}" if detail else ""
            raise CodexCliExecutionError(
                f"Codex agent exited with code {completed.returncode}{suffix}",
                returncode=completed.returncode,
                metadata=metadata,
            )
        metadata = _metadata(
            request=request,
            invocation=invocation,
            prompt=prompt,
            reasoning=reasoning,
            sandbox=sandbox,
            started_at=started_at,
            ended_at=ended_at,
            latency_ms=latency_ms,
            returncode=completed.returncode,
            stderr=completed.stderr,
            raw_jsonl=completed.stdout,
            parsed=parsed,
        )
        return AgentOutcome(
            output_text=parsed.final_message,
            commands=tuple(parsed.commands),
            input_tokens=parsed.input_tokens,
            cached_input_tokens=parsed.cached_input_tokens,
            output_tokens=parsed.output_tokens,
            tool_calls=len(parsed.command_events) + len(parsed.tool_events),
            metadata=metadata,
        )


# Short spelling for callers that name adapters after their backing service.
CodexCliAdapter = CodexCliAgentAdapter


def render_codex_prompt(request: ReplayRequest) -> str:
    """Render one task followed by every eagerly supplied context source."""

    parts = [
        "You are executing exactly one ContextLens replay task in the current "
        "isolated workspace.",
        "Use the supplied context while completing the task.",
        "",
        f'<task task_id="{escape(request.task.task_id, quote=True)}">',
        request.task.instruction,
        "</task>",
        "",
        "<context_sources>",
    ]
    for source in request.context:
        if source.content is None:
            raise ValueError(
                "Codex CLI replay requires inline content for context source "
                f"{source.source_id!r}"
            )
        parts.extend(
            (
                (
                    '<context_source source_id="'
                    f"{escape(source.source_id, quote=True)}"
                    '" kind="'
                    f"{escape(source.kind.value, quote=True)}"
                    '" name="'
                    f'{escape(source.name, quote=True)}">'
                ),
                source.content,
                "</context_source>",
            )
        )
    parts.extend(("</context_sources>", ""))
    return "\n".join(parts)


def _parse_jsonl(raw_jsonl: str) -> _ParsedEvents:
    parsed = _ParsedEvents()
    for line_number, raw_line in enumerate(raw_jsonl.splitlines(), start=1):
        line = raw_line.removeprefix("\ufeff").strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            parsed.parse_errors.append(
                f"line {line_number}: invalid JSON ({error.msg})"
            )
            continue
        if not isinstance(value, dict):
            parsed.parse_errors.append(
                f"line {line_number}: event must be a JSON object"
            )
            continue
        event = {str(key): item for key, item in value.items()}
        parsed.events.append(event)
        event_type = _optional_string(event.get("type")) or ""
        thread_id = _optional_string(event.get("thread_id"))
        if thread_id and (
            event_type == "thread.started" or parsed.thread_id is None
        ):
            parsed.thread_id = thread_id

        usage = _find_usage(event)
        if usage is not None:
            input_tokens, cached_input_tokens, output_tokens = usage
            if input_tokens is not None:
                parsed.input_tokens = input_tokens
            if cached_input_tokens is not None:
                parsed.cached_input_tokens = cached_input_tokens
            if output_tokens is not None:
                parsed.output_tokens = output_tokens

        item = event.get("item")
        normalized_item = (
            {str(key): item_value for key, item_value in item.items()}
            if isinstance(item, dict)
            else None
        )
        if normalized_item is not None:
            _parse_item(event_type, normalized_item, parsed)
        elif event_type in {"agent_message", "message.completed"}:
            message = _optional_string(event.get("text"))
            if message:
                parsed.final_message = message

        error_message = _event_error(event_type, event, normalized_item)
        if error_message is not None:
            parsed.errors.append(error_message)
    return parsed


def _parse_item(
    event_type: str,
    item: dict[str, Any],
    parsed: _ParsedEvents,
) -> None:
    item_type = _optional_string(item.get("type")) or ""
    is_completed = event_type in {"item.completed", "item.failed"}
    if is_completed and item_type in {"agent_message", "message"}:
        message = _optional_string(item.get("text"))
        if message:
            parsed.final_message = message
        return
    if not is_completed or item_type in {"reasoning", "analysis"}:
        return
    event_record = {"event_type": event_type, "item": item}
    if item_type in {"command_execution", "command", "shell_command"}:
        command = _render_command(item.get("command"))
        if command is not None:
            parsed.commands.append(command)
        parsed.command_events.append(event_record)
    else:
        parsed.tool_events.append(event_record)
    parsed.all_tool_events.append(event_record)


def _find_usage(
    event: Mapping[str, Any],
) -> tuple[int | None, int | None, int | None] | None:
    candidates: list[Mapping[str, Any]] = []
    for key in ("usage", "token_usage"):
        value = event.get(key)
        if isinstance(value, Mapping):
            candidates.append(value)
    response = event.get("response")
    if isinstance(response, Mapping):
        for key in ("usage", "token_usage"):
            value = response.get(key)
            if isinstance(value, Mapping):
                candidates.append(value)
    for usage in candidates:
        input_tokens = _nonnegative_int(
            usage.get("input_tokens", usage.get("input"))
        )
        cached_tokens = _nonnegative_int(
            usage.get("cached_input_tokens", usage.get("cached"))
        )
        details = usage.get("input_tokens_details")
        if cached_tokens is None and isinstance(details, Mapping):
            cached_tokens = _nonnegative_int(details.get("cached_tokens"))
        output_tokens = _nonnegative_int(
            usage.get("output_tokens", usage.get("output"))
        )
        if any(
            value is not None
            for value in (input_tokens, cached_tokens, output_tokens)
        ):
            return input_tokens, cached_tokens, output_tokens
    return None


def _event_error(
    event_type: str,
    event: Mapping[str, Any],
    item: Mapping[str, Any] | None,
) -> str | None:
    failed_event = event_type in {"error", "turn.failed", "item.failed"}
    failed_item = item is not None and item.get("status") == "failed"
    if not failed_event and not failed_item:
        return None
    for container in (item, event):
        if container is None:
            continue
        for key in ("error", "message", "detail"):
            if key in container:
                return _render_error(container[key])
    return f"Codex event reported failure ({event_type or 'unknown event'})"


def _render_error(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        message = value.get("message")
        if isinstance(message, str):
            return message
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _render_command(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        return " ".join(value)
    return None


def _metadata(
    *,
    request: ReplayRequest,
    invocation: tuple[str, ...],
    prompt: str,
    reasoning: str,
    sandbox: str,
    started_at: str,
    ended_at: str,
    latency_ms: int,
    returncode: int | None,
    stderr: str,
    raw_jsonl: str,
    parsed: _ParsedEvents,
) -> dict[str, Any]:
    return {
        "adapter": "codex-cli",
        "provider": request.settings.provider,
        "model": request.settings.model,
        "reasoning_effort": reasoning,
        "sandbox": sandbox,
        "thread_id": parsed.thread_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "latency_ms": latency_ms,
        "workspace": request.workspace,
        "invocation_command": list(invocation),
        "rendered_prompt": prompt,
        "raw_jsonl": raw_jsonl,
        "raw_model_response": parsed.final_message,
        "empty_final_message": not parsed.final_message,
        "jsonl_events": parsed.events,
        "command_events": parsed.command_events,
        "tool_events": parsed.tool_events,
        "tool_calls": parsed.all_tool_events,
        "provider_usage": {
            "input_tokens": parsed.input_tokens,
            "cached_input_tokens": parsed.cached_input_tokens,
            "output_tokens": parsed.output_tokens,
        },
        "cached_input_tokens": parsed.cached_input_tokens,
        "errors": parsed.errors,
        "jsonl_parse_errors": parsed.parse_errors,
        "stderr": stderr,
        "returncode": returncode,
        "unsupported_settings": _unsupported_settings(request),
    }


def _unsupported_settings(request: ReplayRequest) -> dict[str, Any]:
    unsupported: dict[str, Any] = {}
    if request.settings.seed is not None:
        unsupported["seed"] = request.settings.seed
    if request.settings.temperature is not None:
        unsupported["temperature"] = request.settings.temperature
    if request.settings.tools:
        unsupported["tools"] = list(request.settings.tools)
    recognized = {
        "reasoning_effort",
        "model_reasoning_effort",
        "reasoning",
        "sandbox",
        "sandbox_mode",
    }
    parameters = {
        key: value
        for key, value in request.settings.parameters.items()
        if key not in recognized
    }
    if parameters:
        unsupported["parameters"] = parameters
    return unsupported


def _string_parameter(
    parameters: Mapping[str, Any],
    keys: tuple[str, ...],
    default: str,
) -> str:
    for key in keys:
        if key not in parameters:
            continue
        value = parameters[key]
        if not isinstance(value, str):
            raise ValueError(f"agent setting {key!r} must be a string")
        return value
    return default


def _validate_reasoning(value: str) -> None:
    if not value.strip():
        raise ValueError("reasoning effort cannot be empty")


def _validate_sandbox(value: str) -> None:
    if value not in _SANDBOX_MODES:
        allowed = ", ".join(sorted(_SANDBOX_MODES))
        raise ValueError(f"sandbox must be one of: {allowed}")


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
