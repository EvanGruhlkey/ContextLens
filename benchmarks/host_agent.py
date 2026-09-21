"""Host-owned coding agent with transparent tool-result filtering.

The same tools and task prompt are used for every condition. ContextLens
transforms tool output on the response path before the coding model sees it.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from contextlens.context_adapter import (
    Answer,
    ContextAdapter,
    HostTool,
    Message,
    ToolCall,
    TurnLimitExceeded,
)
from contextlens.filtering import FilterConfig, FilterSession
from contextlens.observations import ObservationStore
from contextlens.pruning.model import estimate_tokens
from contextlens.pruning.receipts import ReceiptStore

CODING_POLICIES = ("baseline", "jev_filter", "contextlens")
DEFAULT_MAX_TURNS = 20
DEFAULT_COMMAND_TIMEOUT = 30

TOOL_SCHEMAS = (
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a text file. Optional 1-based start_line and end_line "
                "limit the range."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents for a regex pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Run a shell command in the repository workspace.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Apply a unified diff to the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"patch": {"type": "string"}},
                "required": ["patch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recover",
            "description": (
                "Restore omitted content from a receipt= handle in an omitted "
                "marker."
            ),
            "parameters": {
                "type": "object",
                "properties": {"handle": {"type": "string"}},
                "required": ["handle"],
            },
        },
    },
)

class AgentUnavailable(RuntimeError):
    """No coding-model endpoint could be used."""


class WorkspaceRepository:
    """Expose recover as a host method so the tool set stays identical."""

    def __init__(
        self,
        receipts: ReceiptStore,
        session: FilterSession | None,
        recovered: list[int],
    ) -> None:
        self.receipts = receipts
        self.session = session
        self.recovered = recovered

    def call(self, operation: str, arguments: Mapping[str, Any]) -> str:
        if operation != "recover":
            return "Tool unavailable. Choose a registered tool."
        handle = arguments.get("handle") or arguments.get("receipt_id")
        if not isinstance(handle, str) or not handle.strip():
            return "recover requires a handle from an omitted marker."
        if self.session is not None:
            text = self.session.recover(handle.strip())
        else:
            text = self.receipts.read(handle.strip())
        self.recovered.append(estimate_tokens(text))
        return text


def coding_prompt(task: str) -> str:
    return (
        "Resolve this repository task with a minimal patch. Search, read, edit and "
        "test with the available tools. Do not inspect parent directories, access "
        "the network or inspect Git history. Do not commit or push.\n\nTask:\n"
        + task
    )


def _slice(text: str, start: int | None, end: int | None) -> str:
    if start is None and end is None:
        return text
    lines = text.splitlines(keepends=True)
    begin = (start or 1) - 1
    stop = end or len(lines)
    return "".join(lines[max(0, begin) : max(0, stop)])


def _int_arg(arguments: Mapping[str, Any], name: str) -> int | None:
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def workspace_tools(workspace: Path) -> dict[str, HostTool]:
    def read_file(arguments: Mapping[str, Any]) -> str:
        relative = arguments.get("path")
        if not isinstance(relative, str) or not relative.strip():
            return "read_file requires path."
        path = (workspace / relative).resolve()
        if workspace.resolve() not in path.parents and path != workspace.resolve():
            return "Path is outside the workspace."
        if not path.is_file():
            return f"File not found: {relative}"
        return _slice(
            path.read_text(encoding="utf-8", errors="replace"),
            _int_arg(arguments, "start_line"),
            _int_arg(arguments, "end_line"),
        )

    def grep(arguments: Mapping[str, Any]) -> str:
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return "grep requires pattern."
        target = arguments.get("path") or "."
        command = ["rg", "--line-number", "--no-heading", "--color", "never", pattern]
        glob = arguments.get("glob")
        if isinstance(glob, str) and glob:
            command.extend(["--glob", glob])
        command.append(str(target))
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DEFAULT_COMMAND_TIMEOUT,
            check=False,
        )
        output = completed.stdout or completed.stderr
        if completed.returncode not in {0, 1}:
            return output or f"grep failed with code {completed.returncode}"
        return output or "(no matches)"

    def shell(arguments: Mapping[str, Any]) -> str:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return "shell requires command."
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DEFAULT_COMMAND_TIMEOUT,
            check=False,
            shell=True,
        )
        chunks = []
        if completed.stdout:
            chunks.append(completed.stdout)
        if completed.stderr:
            chunks.append(completed.stderr)
        chunks.append(f"exit={completed.returncode}")
        return "\n".join(chunks)

    def apply_patch(arguments: Mapping[str, Any]) -> str:
        patch = arguments.get("patch")
        if not isinstance(patch, str) or not patch.strip():
            return "apply_patch requires patch."
        completed = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=workspace,
            input=patch.encode(),
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            return completed.stderr.decode(errors="replace") or "apply_patch failed"
        return "patch applied"

    return {
        "read_file": HostTool(read_file, filter_output=True),
        "grep": HostTool(grep, filter_output=True),
        "shell": HostTool(shell, filter_output=True),
        "apply_patch": HostTool(apply_patch, filter_output=False),
    }


def _filter_tools(policy: str, tools: dict[str, HostTool]) -> dict[str, HostTool]:
    enabled = policy != "baseline"
    return {
        name: HostTool(
            tool.execute,
            prune=False,
            filter_output=enabled and tool.filter_output,
        )
        for name, tool in tools.items()
    }


def _messages_for_api(history: Sequence[Message]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    pending_id: str | None = None
    for index, message in enumerate(history):
        if message.role == "system":
            if message.content.strip():
                payload.append({"role": "system", "content": message.content})
            continue
        if message.role == "user":
            payload.append({"role": "user", "content": message.content})
            continue
        if message.role == "assistant" and message.tool:
            pending_id = f"call_{index}"
            payload.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": pending_id,
                            "type": "function",
                            "function": {
                                "name": message.tool,
                                "arguments": json.dumps(dict(message.arguments)),
                            },
                        }
                    ],
                }
            )
            continue
        if message.role == "tool":
            payload.append(
                {
                    "role": "tool",
                    "tool_call_id": pending_id or f"call_{index}",
                    "content": message.content,
                }
            )
            continue
        payload.append({"role": "assistant", "content": message.content})
    return payload


def _usage_from_response(data: Mapping[str, Any]) -> dict[str, int]:
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    details = usage.get("prompt_tokens_details") or usage.get(
        "input_tokens_details"
    ) or {}
    cached = 0
    if isinstance(details, dict):
        raw_cached = details.get(
            "cached_tokens", details.get("cache_read_input_tokens")
        )
        if isinstance(raw_cached, int) and not isinstance(raw_cached, bool):
            cached = raw_cached
    if isinstance(prompt, int) and not isinstance(prompt, bool):
        input_tokens = prompt
    else:
        input_tokens = 0
    if isinstance(completion, int) and not isinstance(completion, bool):
        output_tokens = completion
    else:
        output_tokens = 0
    if cached > input_tokens:
        cached = 0
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "uncached_input_tokens": input_tokens - cached,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def _endpoint() -> tuple[str, str]:
    gateway = os.environ.get("AI_GATEWAY_API_KEY", "").strip()
    openai = os.environ.get("OPENAI_API_KEY", "").strip()
    base = os.environ.get("OPENAI_BASE_URL", "").strip().rstrip("/")
    if gateway:
        return base or "https://ai-gateway.vercel.sh/v1", gateway
    if openai:
        return base or "https://api.openai.com/v1", openai
    raise AgentUnavailable(
        "Set OPENAI_API_KEY or AI_GATEWAY_API_KEY for the coding agent"
    )


def http_solver(
    model: str,
    *,
    timeout: int,
    usage: dict[str, int],
) -> Callable[[Sequence[Message]], ToolCall | Answer]:
    url, key = _endpoint()

    def solver(history: Sequence[Message]) -> ToolCall | Answer:
        request = urllib.request.Request(
            url + "/chat/completions",
            data=json.dumps(
                {
                    "model": model,
                    "messages": _messages_for_api(history),
                    "tools": list(TOOL_SCHEMAS),
                    "tool_choice": "auto",
                },
                ensure_ascii=False,
            ).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=min(timeout, 120)) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise AgentUnavailable(f"coding model HTTP {error.code}") from None
        except (OSError, ValueError) as error:
            raise AgentUnavailable("coding model request failed") from error
        for field, value in _usage_from_response(data).items():
            usage[field] = usage.get(field, 0) + value
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AgentUnavailable("coding model returned no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise AgentUnavailable("coding model returned an invalid message")
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            first = tool_calls[0] if isinstance(tool_calls[0], dict) else {}
            function = first.get("function") if isinstance(first, dict) else {}
            name = function.get("name") if isinstance(function, dict) else None
            raw_args = function.get("arguments") if isinstance(function, dict) else "{}"
            try:
                parsed = json.loads(raw_args or "{}")
            except json.JSONDecodeError:
                parsed = {}
            if not isinstance(name, str) or not name:
                return Answer(str(message.get("content") or ""))
            return ToolCall(name, parsed if isinstance(parsed, dict) else {})
        return Answer(str(message.get("content") or ""))

    return solver


def _filter_metrics(session: FilterSession | None, policy: str) -> dict[str, Any]:
    if session is None or policy == "baseline":
        return {
            "filter_calls": 0,
            "raw_tool_output_tokens": 0,
            "injected_tool_output_tokens": 0,
            "tool_output_tokens_removed": 0,
            "tool_output_reduction_percent": 0.0,
            "jev_input_tokens": 0,
            "jev_output_tokens": 0,
            "jev_cost": "0",
        }
    raw = sum(item.original_tokens for item in session.results)
    injected = sum(item.retained_tokens for item in session.results)
    removed = max(0, raw - injected)
    cost = Decimal("0")
    for item in session.results:
        if not item.jev_cost:
            continue
        try:
            cost += Decimal(item.jev_cost)
        except InvalidOperation:
            continue
    percent = round(100 * removed / raw, 2) if raw else 0.0
    return {
        "filter_calls": sum(1 for item in session.results if item.backend == "jev"),
        "raw_tool_output_tokens": raw,
        "injected_tool_output_tokens": injected,
        "tool_output_tokens_removed": removed,
        "tool_output_reduction_percent": percent,
        "jev_input_tokens": sum(item.jev_input_tokens for item in session.results),
        "jev_output_tokens": sum(item.jev_output_tokens for item in session.results),
        "jev_cost": format(cost, "f"),
    }


def _baseline_tool_tokens(adapter: ContextAdapter) -> dict[str, Any]:
    tokens = 0
    for message in adapter.history:
        if message.role == "tool":
            tokens += estimate_tokens(message.content)
    return {
        "filter_calls": 0,
        "raw_tool_output_tokens": tokens,
        "injected_tool_output_tokens": tokens,
        "tool_output_tokens_removed": 0,
        "tool_output_reduction_percent": 0.0,
        "jev_input_tokens": 0,
        "jev_output_tokens": 0,
        "jev_cost": "0",
    }


def run_host_agent(
    workspace: Path,
    task: str,
    policy: str,
    *,
    model: str,
    timeout: int,
    max_turns: int = DEFAULT_MAX_TURNS,
    state: Path,
    solver: Callable[[Sequence[Message]], ToolCall | Answer] | None = None,
    usage: dict[str, int] | None = None,
    judge: Any = None,
) -> dict[str, Any]:
    if policy not in CODING_POLICIES:
        raise ValueError("unknown coding-agent policy")
    receipts = ReceiptStore(state / "receipts")
    observations = ObservationStore(state / "observations")
    config = FilterConfig.from_env()
    if policy == "jev_filter":
        config = FilterConfig(
            minimum_tokens=config.minimum_tokens,
            keep_threshold=config.keep_threshold,
            narrow_range_lines=config.narrow_range_lines,
            max_candidates=config.max_candidates,
            dependency_hops=config.dependency_hops,
            symbol_passthrough_tokens=config.symbol_passthrough_tokens,
            expand_structure=False,
        )
    session: FilterSession | None = None
    if policy != "baseline":
        session = FilterSession(
            receipts, observations, task=task, judge=judge, config=config
        )
    recovered: list[int] = []
    usage = usage if usage is not None else {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "uncached_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    adapter = ContextAdapter(
        WorkspaceRepository(receipts, session, recovered),
        receipts,
        filter_session=session,
        tools=_filter_tools(policy, workspace_tools(workspace)),
    )
    worker = solver
    prompt = coding_prompt(task)
    started = time.perf_counter()
    status = "completed"
    try:
        if worker is None:
            worker = http_solver(model, timeout=timeout, usage=usage)
        adapter.run(worker, prompt, max_turns=max_turns)
    except TurnLimitExceeded:
        status = "turn_limit"
    except AgentUnavailable as error:
        status = "agent_unavailable"
        (state / "error.txt").write_text(str(error), encoding="utf-8")
    except Exception as error:
        status = "agent_process_error"
        (state / "error.txt").write_text(
            f"{type(error).__name__}: {error}", encoding="utf-8"
        )
    elapsed = time.perf_counter() - started
    if elapsed > timeout and status == "completed":
        status = "timeout"
    metrics = (
        _baseline_tool_tokens(adapter)
        if policy == "baseline"
        else _filter_metrics(session, policy)
    )
    turns = sum(1 for message in adapter.history if message.role == "assistant")
    row = {
        "status": status,
        "agent_seconds": elapsed,
        "agent_turns": turns,
        "recovery_calls": (
            len(recovered) if session is None else session.recovery_calls
        ),
        "recovered_tokens": sum(recovered),
        "history": [
            {"role": message.role, "tool": message.tool, "content": message.content}
            for message in adapter.history
        ],
        **usage,
        **metrics,
    }
    return row
