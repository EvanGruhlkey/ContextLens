"""Minimal MCP server: prune a tool result, recover what was omitted.

Two tools, both transparent to the agent's own planning. ``context_prune``
reduces one large observation; ``context_recover`` returns the exact original
text behind a receipt handle. Nothing here chooses the agent's next action.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from contextlens.filtering import PruneConfig, PruneRequest, PruneSession
from contextlens.jev import Judge
from contextlens.receipts import ReceiptStore

PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
MAX_LINE_BYTES = 1024 * 1024
INSTRUCTIONS = (
    "ContextLens reduces large tool results before they reach the coding "
    "model. Send an oversized observation to context_prune and use "
    "context_recover with the receipt in an omission marker when you need the "
    "omitted text. ContextLens only decides what is relevant; it does not "
    "choose your next action."
)

_STRING = {"type": "string"}
_LINE = {"type": "integer", "minimum": 1}

TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "context_prune",
        "description": (
            "Prune one large tool result before the coding model reads it. "
            "Returns the kept content with receipts for anything omitted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "output": _STRING,
                "task": _STRING,
                "focus": _STRING,
                "tool": _STRING,
                "path": _STRING,
                "command": _STRING,
            },
            "required": ["output"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False},
    },
    {
        "name": "context_recover",
        "description": (
            "Return the exact original text behind a receipt handle, "
            "optionally one line range."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "receipt_id": _STRING,
                "start_line": _LINE,
                "end_line": _LINE,
            },
            "required": ["receipt_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
)


class Server:
    """One receipt store and one task for the lifetime of a session."""

    def __init__(
        self,
        receipts: ReceiptStore,
        *,
        task: str = "",
        judge: Judge | None = None,
        config: PruneConfig | None = None,
    ) -> None:
        self.receipts = receipts
        self.task = " ".join(task.split())
        self.session = PruneSession(
            receipts,
            task=self.task or "repository task",
            judge=judge,
            config=config,
        )

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "context_prune":
            return self._prune(arguments)
        if name == "context_recover":
            return self.receipts.read(
                _string(arguments, "receipt_id"),
                start_line=_line(arguments, "start_line"),
                end_line=_line(arguments, "end_line"),
            )
        raise ValueError("unknown tool")

    def _prune(self, arguments: dict[str, Any]) -> str:
        task = _string(arguments, "task", self.task) or self.session.task
        self.session.task = task
        tool_arguments: dict[str, Any] = {
            key: arguments[key] for key in ("path", "command") if key in arguments
        }
        outcome = self.session.pruner.prune(
            PruneRequest(
                task=task,
                output=_string(arguments, "output"),
                tool=_string(arguments, "tool", "tool"),
                arguments=tool_arguments,
                focus=_string(arguments, "focus", ""),
            )
        )
        self.session.outcomes.append(outcome)
        if not outcome.omitted_ranges:
            return outcome.text
        return (
            f"{outcome.text}\n[contextlens kept {outcome.kept_chunks} of "
            f"{outcome.chunks} chunks; recover the full output with "
            f"context_recover {outcome.receipt_id}]"
        )


def dispatch(server: Server, message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "invalid request"},
        }
    if "id" not in message:
        return None
    response: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
    params = message.get("params", {})
    if not isinstance(params, dict):
        response["error"] = {"code": -32602, "message": "params must be an object"}
        return response
    method = message.get("method")
    if method == "initialize":
        version = params.get("protocolVersion")
        response["result"] = {
            "protocolVersion": version
            if isinstance(version, str) and version in PROTOCOLS
            else PROTOCOLS[-1],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "contextlens", "version": "0.2.0"},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        response["result"] = {}
    elif method == "tools/list":
        response["result"] = {"tools": [dict(tool) for tool in TOOLS]}
    elif method == "tools/call":
        response["result"] = _call(server, params)
    else:
        response["error"] = {"code": -32601, "message": "method not found"}
    return response


def _call(server: Server, params: dict[str, Any]) -> dict[str, Any]:
    definitions = {tool["name"]: tool for tool in TOOLS}
    name = params.get("name")
    arguments = params.get("arguments", {})
    try:
        if not isinstance(name, str) or name not in definitions:
            raise ValueError("unknown tool")
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        schema = definitions[name]["inputSchema"]
        properties = schema["properties"]
        if set(arguments) - set(properties):
            raise ValueError("unknown tool argument")
        if set(schema["required"]) - set(arguments):
            raise ValueError("missing required argument")
        for key, value in arguments.items():
            expected = properties[key]["type"]
            if expected == "string" and not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            if expected == "integer" and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise ValueError(f"{key} must be a positive integer")
        return {
            "content": [{"type": "text", "text": server.call(name, arguments)}],
            "isError": False,
        }
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        return {"content": [{"type": "text", "text": str(error)}], "isError": True}


def serve_stdio(
    receipts: ReceiptStore,
    *,
    task: str = "",
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> None:
    server = Server(receipts, task=task)
    while line := input_stream.readline(MAX_LINE_BYTES + 1):
        try:
            if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                raise ValueError("request exceeds 1 MiB")
            result = dispatch(server, json.loads(line))
        except (TypeError, ValueError) as error:
            result = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(error)},
            }
        if result is not None:
            output_stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            output_stream.flush()


def _string(arguments: dict[str, Any], name: str, default: str | None = None) -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _line(arguments: dict[str, Any], name: str) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value
