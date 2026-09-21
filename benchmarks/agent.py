"""Host-owned coding agent with transparent ContextLens layers.

Every condition gets the same task text, tool set, model, reasoning effort,
timeout, turn limit, and hidden grader. The agent is never told that
ContextLens exists:

    baseline          raw tool output, transcript grows untouched
    live_pruning      large tool results pruned before the model reads them
    compaction_only   raw tool output, but stale tool interactions compacted
                      out of the transcript once it passes the threshold
    full_contextlens  both layers
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextlens.compaction import CompactionConfig, compact_transcript
from contextlens.filtering import PruneConfig, PruneSession
from contextlens.jev import JevGateway, Judge
from contextlens.models import (
    Message,
    ToolResult,
    ToolUse,
    estimate_tokens,
    transcript_tokens,
)
from contextlens.receipts import ReceiptStore

CONDITIONS = ("baseline", "live_pruning", "compaction_only", "full_contextlens")
LIVE_PRUNING_CONDITIONS = frozenset({"live_pruning", "full_contextlens"})
COMPACTION_CONDITIONS = frozenset({"compaction_only", "full_contextlens"})
DEFAULT_MAX_TURNS = 20
DEFAULT_COMMAND_TIMEOUT = 30
MAX_TOOL_OUTPUT_CHARS = 400_000

TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "name": "read_file",
        "description": (
            "Read a text file. Optional 1-based start_line and end_line limit "
            "the range."
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
    {
        "name": "grep",
        "description": "Search file contents for a regular expression.",
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
    {
        "name": "shell",
        "description": "Run a shell command in the repository workspace.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "apply_patch",
        "description": "Apply a unified diff to the workspace.",
        "parameters": {
            "type": "object",
            "properties": {"patch": {"type": "string"}},
            "required": ["patch"],
        },
    },
    {
        "name": "recover",
        "description": (
            "Return omitted content in full, using the receipt= handle printed "
            "in an omitted marker."
        ),
        "parameters": {
            "type": "object",
            "properties": {"receipt": {"type": "string"}},
            "required": ["receipt"],
        },
    },
)


class AgentUnavailable(RuntimeError):
    """No coding-model endpoint could be used."""


class TurnLimitExceeded(RuntimeError):
    """The agent used its whole turn budget without answering."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any]
    call_id: str


@dataclass(frozen=True, slots=True)
class Answer:
    text: str


Solver = Callable[[Sequence[Message]], "ToolCall | Answer"]


@dataclass(slots=True)
class RunMetrics:
    """Everything the paired report needs from one attempt."""

    status: str = "completed"
    agent_seconds: float = 0.0
    agent_turns: int = 0
    tool_calls: int = 0
    raw_tool_output_tokens: int = 0
    injected_tool_output_tokens: int = 0
    compaction_attempts: int = 0
    compaction_events: int = 0
    compaction_tokens_removed: int = 0
    final_transcript_tokens: int = 0
    recovery_calls: int = 0
    recovered_tokens: int = 0
    prune_reasons: dict[str, int] = field(default_factory=dict)
    compaction_reasons: dict[str, int] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    jev: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        removed = max(
            0, self.raw_tool_output_tokens - self.injected_tool_output_tokens
        )
        return {
            "status": self.status,
            "agent_seconds": self.agent_seconds,
            "agent_turns": self.agent_turns,
            "tool_calls": self.tool_calls,
            "compaction_triggered": self.compaction_events > 0,
            "raw_tool_output_tokens": self.raw_tool_output_tokens,
            "injected_tool_output_tokens": self.injected_tool_output_tokens,
            "tool_output_tokens_removed": removed,
            "tool_output_reduction_percent": (
                round(100 * removed / self.raw_tool_output_tokens, 2)
                if self.raw_tool_output_tokens
                else 0.0
            ),
            "compaction_attempts": self.compaction_attempts,
            "compaction_events": self.compaction_events,
            "compaction_tokens_removed": self.compaction_tokens_removed,
            "final_transcript_tokens": self.final_transcript_tokens,
            "recovery_calls": self.recovery_calls,
            "recovered_tokens": self.recovered_tokens,
            "prune_reasons": dict(self.prune_reasons),
            "compaction_reasons": dict(self.compaction_reasons),
            **{
                key: self.usage.get(key, 0)
                for key in (
                    "input_tokens",
                    "cached_input_tokens",
                    "uncached_input_tokens",
                    "output_tokens",
                    "total_tokens",
                )
            },
            "jev_requests": self.jev.get("jev_requests", 0),
            "jev_input_tokens": self.jev.get("jev_input_tokens", 0),
            "jev_output_tokens": self.jev.get("jev_output_tokens", 0),
            "jev_cost": self.jev.get("jev_cost", "0"),
        }


def coding_prompt(task: str) -> str:
    return (
        "Resolve this repository task with a minimal patch. Search, read, edit "
        "and test with the available tools. Do not inspect parent directories, "
        "access the network or inspect Git history. Do not commit or push."
        "\n\nTask:\n" + task
    )


_SENSITIVE = ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def sanitized_environment() -> dict[str, str]:
    """The environment tools run in, without the harness's own credentials.

    The agent's `shell` tool would otherwise be able to read the coding-model
    and gateway keys and print them into a transcript that gets saved.
    """

    return {
        name: value
        for name, value in os.environ.items()
        if not any(marker in name.upper() for marker in _SENSITIVE)
        and name not in {"OPENAI_BASE_URL"}
    }


def workspace_tools(workspace: Path) -> dict[str, Callable[[Mapping[str, Any]], str]]:
    """The identical tool set every condition gets."""

    root = workspace.resolve()
    environment = sanitized_environment()

    def read_file(arguments: Mapping[str, Any]) -> str:
        relative = arguments.get("path")
        if not isinstance(relative, str) or not relative.strip():
            return "read_file requires path."
        path = (root / relative).resolve()
        if path != root and root not in path.parents:
            return "Path is outside the workspace."
        if not path.is_file():
            return f"File not found: {relative}"
        text = path.read_text(encoding="utf-8", errors="replace")
        start = _int(arguments, "start_line")
        end = _int(arguments, "end_line")
        if start is None and end is None:
            return text
        lines = text.splitlines(keepends=True)
        return "".join(lines[max(0, (start or 1) - 1) : max(0, end or len(lines))])

    def grep(arguments: Mapping[str, Any]) -> str:
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return "grep requires pattern."
        command = ["rg", "--line-number", "--no-heading", "--color", "never", pattern]
        glob = arguments.get("glob")
        if isinstance(glob, str) and glob:
            command += ["--glob", glob]
        target = arguments.get("path")
        command.append(target if isinstance(target, str) and target else ".")
        completed = _run(command, root, shell=False, env=environment)
        output = completed.stdout or completed.stderr
        if completed.returncode not in {0, 1}:
            return output or f"grep failed with code {completed.returncode}"
        return output or "(no matches)"

    def shell(arguments: Mapping[str, Any]) -> str:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return "shell requires command."
        completed = _run(command, root, shell=True, env=environment)
        parts = [part for part in (completed.stdout, completed.stderr) if part]
        parts.append(f"exit={completed.returncode}")
        return "\n".join(parts)

    def apply_patch(arguments: Mapping[str, Any]) -> str:
        patch = arguments.get("patch")
        if not isinstance(patch, str) or not patch.strip():
            return "apply_patch requires patch."
        completed = subprocess.run(
            ("git", "apply", "--whitespace=nowarn", "-"),
            cwd=root,
            input=patch.encode(),
            capture_output=True,
            timeout=DEFAULT_COMMAND_TIMEOUT,
            check=False,
            env=environment,
        )
        if completed.returncode:
            return completed.stderr.decode(errors="replace") or "apply_patch failed"
        return "patch applied"

    return {
        "read_file": read_file,
        "grep": grep,
        "shell": shell,
        "apply_patch": apply_patch,
    }


PRUNABLE_TOOLS = frozenset({"read_file", "grep", "shell"})


def run_agent(
    workspace: Path,
    task: str,
    condition: str,
    *,
    state: Path,
    model: str = "gpt-5.6-luna",
    timeout: int = 300,
    max_turns: int = DEFAULT_MAX_TURNS,
    solver: Solver | None = None,
    judge: Judge | None = None,
    prune_config: PruneConfig | None = None,
    compaction_config: CompactionConfig | None = None,
) -> tuple[RunMetrics, tuple[Message, ...]]:
    """Run one attempt and return its metrics with the final transcript."""

    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition!r}")
    state.mkdir(parents=True, exist_ok=True)
    receipts = ReceiptStore(state / "receipts")
    metrics = RunMetrics()
    usage: dict[str, int] = {}
    session: PruneSession | None = None
    if condition in LIVE_PRUNING_CONDITIONS:
        session = PruneSession(
            receipts,
            task=task,
            judge=judge if judge is not None else JevGateway(),
            config=prune_config,
        )
    compactor = (
        compaction_config or CompactionConfig()
        if condition in COMPACTION_CONDITIONS
        else None
    )
    tools = workspace_tools(workspace)
    prompt = coding_prompt(task)
    messages: list[Message] = [Message("user", prompt)]
    worker = solver
    started = time.perf_counter()
    try:
        if worker is None:
            worker = http_solver(model, timeout=timeout, usage=usage)
        for _ in range(max_turns):
            action = worker(tuple(messages))
            metrics.agent_turns += 1
            if isinstance(action, Answer):
                messages.append(Message("assistant", action.text))
                break
            messages.append(
                Message(
                    "assistant",
                    "",
                    (
                        ToolUse(
                            action.call_id, action.name, dict(action.arguments)
                        ),
                    ),
                )
            )
            raw, is_error = _execute(action, tools, receipts)
            metrics.tool_calls += 1
            text = raw
            if session is not None and action.name in PRUNABLE_TOOLS:
                text = session.observe(
                    raw, tool=action.name, arguments=action.arguments
                ).text
            if action.name != "recover":
                metrics.raw_tool_output_tokens += estimate_tokens(raw)
                metrics.injected_tool_output_tokens += estimate_tokens(text)
            messages.append(
                Message(
                    "user",
                    "",
                    (),
                    (ToolResult(action.call_id, text, is_error),),
                    pinned=action.name == "recover",
                )
            )
            if compactor is not None:
                messages = _compact(messages, compactor, judge, receipts, metrics, task)
        else:
            raise TurnLimitExceeded(f"agent exceeded {max_turns} turns")
    except TurnLimitExceeded:
        metrics.status = "turn_limit"
    except TimeoutError:
        metrics.status = "timeout"
    except AgentUnavailable as error:
        metrics.status = "agent_unavailable"
        (state / "error.txt").write_text(str(error), encoding="utf-8")
    except Exception as error:  # noqa: BLE001 - one attempt must not kill the run
        metrics.status = "agent_process_error"
        (state / "error.txt").write_text(
            f"{type(error).__name__}: {error}", encoding="utf-8"
        )
    metrics.agent_seconds = time.perf_counter() - started
    if metrics.agent_seconds > timeout and metrics.status == "completed":
        metrics.status = "timeout"
    metrics.final_transcript_tokens = transcript_tokens(tuple(messages))
    metrics.recovery_calls = receipts.recoveries
    metrics.recovered_tokens = receipts.recovered_tokens
    metrics.usage = dict(usage)
    if session is not None:
        metrics.prune_reasons = session.reasons()
    metrics.jev = _jev_totals(session, metrics)
    return metrics, tuple(messages)


def _execute(
    action: ToolCall,
    tools: Mapping[str, Callable[[Mapping[str, Any]], str]],
    receipts: ReceiptStore,
) -> tuple[str, bool]:
    """Run one tool. ``recover`` is answered by the host, not the model."""

    if action.name == "recover":
        handle = action.arguments.get("receipt")
        if not isinstance(handle, str) or not handle.strip():
            return "recover requires the receipt= handle from an omitted marker.", True
        try:
            return receipts.read(handle.strip()), False
        except (KeyError, RuntimeError, ValueError) as error:
            return f"recover failed: {error}", True
    tool = tools.get(action.name)
    if tool is None:
        return "Tool unavailable. Choose a registered tool.", True
    try:
        output = tool(action.arguments)
    except subprocess.TimeoutExpired:
        return "Tool timed out.", True
    except OSError as error:
        return f"Tool failed: {error}", True
    return output[:MAX_TOOL_OUTPUT_CHARS], False


def _compact(
    messages: list[Message],
    config: CompactionConfig,
    judge: Judge | None,
    receipts: ReceiptStore,
    metrics: RunMetrics,
    task: str,
) -> list[Message]:
    if transcript_tokens(tuple(messages)) < config.trigger_tokens:
        return messages
    metrics.compaction_attempts += 1
    result = compact_transcript(
        messages,
        judge if judge is not None else JevGateway(),
        config=config,
        receipts=receipts,
        task=task,
    )
    metrics.jev.setdefault("compaction", []).append(result.usage.to_dict())
    metrics.compaction_reasons[result.reason] = (
        metrics.compaction_reasons.get(result.reason, 0) + 1
    )
    if not result.compacted:
        return messages
    metrics.compaction_events += 1
    metrics.compaction_tokens_removed += result.tokens_removed
    return list(result.messages)


def _jev_totals(session: PruneSession | None, metrics: RunMetrics) -> dict[str, Any]:
    requests = 0
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    if session is not None:
        totals = session.metrics()
        requests += int(totals["jev_requests"])
        input_tokens += int(totals["jev_input_tokens"])
        output_tokens += int(totals["jev_output_tokens"])
        cost += float(totals["jev_cost"])
    for entry in metrics.jev.get("compaction", []):
        requests += int(entry["jev_requests"])
        input_tokens += int(entry["jev_input_tokens"])
        output_tokens += int(entry["jev_output_tokens"])
        cost += float(entry["jev_cost"])
    return {
        "jev_requests": requests,
        "jev_input_tokens": input_tokens,
        "jev_output_tokens": output_tokens,
        "jev_cost": format(cost, "f"),
    }


def response_input(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Convert the transcript into Responses API input items.

    The whole transcript is sent every turn, so compaction is visible to the
    model exactly as it is to us. All conditions use the same conversion.
    """

    items: list[dict[str, Any]] = []
    for message in messages:
        if message.text.strip():
            items.append({"role": message.role, "content": message.text})
        for use in message.tool_uses:
            items.append(
                {
                    "type": "function_call",
                    "call_id": use.tool_use_id,
                    "name": use.tool,
                    "arguments": json.dumps(dict(use.arguments)),
                }
            )
        for result in message.tool_results:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": result.tool_use_id,
                    "output": result.text,
                }
            )
    return items


def http_solver(
    model: str, *, timeout: int, usage: dict[str, int], effort: str = "low"
) -> Solver:
    """One coding-model turn over the Responses API, stateless per request."""

    url, key = _endpoint()
    deadline = time.perf_counter() + timeout

    def solver(messages: Sequence[Message]) -> ToolCall | Answer:
        remaining = deadline - time.perf_counter()
        if remaining <= 1:
            raise TimeoutError("coding model timeout")
        payload = {
            "model": model,
            "tools": [{"type": "function", **schema} for schema in TOOL_SCHEMAS],
            "parallel_tool_calls": False,
            "store": False,
            "reasoning": {"effort": effort},
            "input": response_input(messages),
        }
        data = _post(
            f"{url}/responses", key, payload, max(5, min(int(remaining), 180))
        )
        for metric, value in _usage(data).items():
            usage[metric] = usage.get(metric, 0) + value
        output = data.get("output")
        if not isinstance(output, list):
            raise AgentUnavailable("coding model returned no output")
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            name = item.get("name")
            call_id = item.get("call_id")
            if not isinstance(name, str) or not isinstance(call_id, str):
                break
            return ToolCall(name, _arguments(item.get("arguments")), call_id)
        return Answer(_text(output) or str(data.get("status") or "done"))

    return solver


def _text(output: Sequence[Any]) -> str:
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
    return "\n".join(texts).strip()


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _usage(data: Mapping[str, Any]) -> dict[str, int]:
    raw_usage = data.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    prompt = _positive(usage.get("input_tokens", usage.get("prompt_tokens")))
    completion = _positive(usage.get("output_tokens", usage.get("completion_tokens")))
    details = usage.get("input_tokens_details")
    if not isinstance(details, dict):
        details = usage.get("prompt_tokens_details")
    cached = 0
    if isinstance(details, dict):
        cached = _positive(
            details.get("cached_tokens", details.get("cache_read_input_tokens"))
        )
    if cached > prompt:
        cached = 0
    return {
        "input_tokens": prompt,
        "cached_input_tokens": cached,
        "uncached_input_tokens": prompt - cached,
        "output_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _positive(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _endpoint() -> tuple[str, str]:
    openai = os.environ.get("OPENAI_API_KEY", "").strip()
    gateway = os.environ.get("AI_GATEWAY_API_KEY", "").strip()
    base = os.environ.get("OPENAI_BASE_URL", "").strip().rstrip("/")
    if openai:
        return base or "https://api.openai.com/v1", openai
    if gateway:
        return base or "https://ai-gateway.vercel.sh/v1", gateway
    raise AgentUnavailable(
        "Set OPENAI_API_KEY or AI_GATEWAY_API_KEY for the coding agent"
    )


def _post(
    url: str, key: str, payload: Mapping[str, Any], timeout: int
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:400]
        raise AgentUnavailable(f"coding model HTTP {error.code}: {detail}") from None
    except (OSError, ValueError) as error:
        raise AgentUnavailable("coding model request failed") from error
    if not isinstance(data, dict):
        raise AgentUnavailable("coding model returned invalid JSON")
    return data


def _run(
    command: str | list[str],
    cwd: Path,
    *,
    shell: bool,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=DEFAULT_COMMAND_TIMEOUT,
        check=False,
        shell=shell,
        env=dict(env) if env is not None else None,
    )


def _int(arguments: Mapping[str, Any], name: str) -> int | None:
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
