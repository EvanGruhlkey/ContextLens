"""Minimal synchronous MCP stdio transport for root-confined evidence tools."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from contextlens.evidence_session import EvidenceSession

PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}


def tool_definitions() -> list[dict[str, Any]]:
    string = {"type": "string"}
    integer = {"type": "integer", "minimum": 1}
    tools = [
        (
            "retrieve",
            "Find verbatim evidence for your current information need. "
            "Expand omitted dependencies before guessing behavior.",
            {
                "task": string,
                "focus": string,
                "budget": integer,
                "response_budget": integer,
            },
            ["task"],
        ),
        (
            "read",
            "Read exact current source with a receipt. Supply expected_hash "
            "to reject stale evidence before editing.",
            {
                "path": string,
                "expected_hash": string,
                "deduplicate": {"type": "boolean"},
                "start_line": integer,
                "end_line": integer,
                "budget": integer,
            },
            ["path"],
        ),
        (
            "expand",
            "Recover exact source or memory snapshots. Verify current source "
            "hashes before editing.",
            {
                "receipt_id": string,
                "start_line": integer,
                "end_line": integer,
                "budget": integer,
            },
            ["receipt_id"],
        ),
        (
            "verify",
            "Check current source against an evidence hash; fails if changed.",
            {"path": string, "expected_hash": string},
            ["path", "expected_hash"],
        ),
        (
            "remember",
            "Offload observations or constraints to recoverable external memory.",
            {"content": string, "label": string, "pinned": {"type": "boolean"}},
            ["content"],
        ),
        (
            "view",
            "Return bounded memory, prioritizing pinned constraints. "
            "Does not rewrite hosted model history.",
            {"budget": integer},
            [],
        ),
        (
            "observe",
            "Optionally prune a new observation with a neural backend. "
            "Preserves full output when unavailable.",
            {
                "task": string,
                "content": string,
                "focus": string,
                "kind": string,
                "language": string,
            },
            ["task", "content"],
        ),
    ]
    return [
        {
            "name": "evidence_" + name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        }
        for name, description, properties, required in tools
    ]


def dispatch(session: EvidenceSession, message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "invalid request"},
        }
    if "id" not in message:
        return None
    response: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
    method = message.get("method")
    params = message.get("params", {})
    if not isinstance(params, dict):
        response["error"] = {"code": -32602, "message": "params must be an object"}
        return response
    if method == "initialize":
        version = params.get("protocolVersion")
        response["result"] = {
            "protocolVersion": version if version in PROTOCOLS else "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "contextlens", "version": "0.1.0"},
            "instructions": "Spans are exact snapshots. Expand missing evidence "
            "and verify hashes before editing.",
        }
    elif method == "ping":
        response["result"] = {}
    elif method == "tools/list":
        response["result"] = {"tools": tool_definitions()}
    elif method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        definitions = {tool["name"]: tool for tool in tool_definitions()}
        if (
            not isinstance(name, str)
            or name not in definitions
            or not isinstance(arguments, dict)
        ):
            response["error"] = {
                "code": -32602,
                "message": "unknown tool or invalid arguments",
            }
            return response
        schema = definitions[name]["inputSchema"]
        try:
            if any(key not in schema["properties"] for key in arguments):
                raise ValueError("unknown tool argument")
            for key in schema["required"]:
                if key not in arguments:
                    raise ValueError(f"missing tool argument: {key}")
            for key, value in arguments.items():
                expected = schema["properties"][key]["type"]
                if (
                    expected == "string"
                    and not isinstance(value, str)
                    or expected == "integer"
                    and (
                        not isinstance(value, int)
                        or isinstance(value, bool)
                        or value < 1
                    )
                    or expected == "boolean"
                    and not isinstance(value, bool)
                ):
                    raise ValueError(f"invalid type or value for {key}")
            result = session.call(name.removeprefix("evidence_"), arguments)
            response["result"] = {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                ],
                "isError": False,
            }
        except (ValueError, KeyError, OSError, RuntimeError) as error:
            response["result"] = {
                "content": [{"type": "text", "text": str(error)}],
                "isError": True,
            }
    else:
        response["error"] = {"code": -32601, "message": "method not found"}
    return response


def serve_stdio(
    session: EvidenceSession,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> None:
    """Read newline-delimited JSON-RPC; stdout contains protocol messages only."""
    while True:
        line = input_stream.readline(16 * 1024 * 1024 + 1)
        if not line:
            return
        try:
            if len(line.encode("utf-8")) > 16 * 1024 * 1024:
                raise ValueError("request exceeds 16 MiB")
            result = dispatch(session, json.loads(line))
        except (ValueError, TypeError) as error:
            result = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(error)},
            }
        if result is not None:
            output_stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            output_stream.flush()
