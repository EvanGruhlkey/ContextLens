"""Lean MCP transport: discovery, verified reads, and snapshot expansion."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from contextlens.action_models import ActionKind
from contextlens.context_tools import RepositoryContext
from contextlens.evidence_mcp import PROTOCOLS
from contextlens.observations import OBSERVATION_KINDS


def tool_definitions(
    *, selection: bool = False, actions: bool = False
) -> list[dict[str, Any]]:
    string = {"type": "string"}
    integer = {"type": "integer", "minimum": 1}
    budget = {"type": "integer", "minimum": 128, "maximum": 16000}
    definitions = [
        (
            "find",
            "Find repository locations on demand. Returns handles, not file bodies.",
            {"query": string, "focus": string, "limit": integer, "budget": budget},
            ["query"],
        ),
        (
            "read",
            "Read exact current source by handle or path. Freshness is checked "
            "internally. Explicit ranges must specify both bounds.",
            {
                "handle": string,
                "path": string,
                "start_line": integer,
                "end_line": integer,
                "budget": budget,
                "reread": {"type": "boolean"},
            },
            [],
        ),
        (
            "expand",
            "Recover a historical source snapshot by handle. "
            "It is not current source or a verified edit target.",
            {
                "handle": string,
                "start_line": integer,
                "end_line": integer,
                "budget": budget,
            },
            ["handle"],
        ),
    ]
    if selection:
        definitions[0] = (
            "select",
            "Select and return exact repository evidence for a task in one call. "
            "Sends task and candidate source to Jev through Vercel Gateway. "
            "Use focus for the immediate question; read known paths directly.",
            {
                "task": string,
                "focus": string,
                "budget": budget,
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            ["task"],
        )
    if actions:
        definitions.append(
            (
                "next",
                "Choose one offered next capability using Jev. The tool validates "
                "bounded actions and never executes them.",
                {
                    "task": string,
                    "focus": string,
                    "observations": {"type": "array", "maxItems": 20},
                    "actions": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": string,
                                "kind": {
                                    "type": "string",
                                    "enum": [kind.value for kind in ActionKind],
                                },
                                "description": string,
                                "tool": string,
                                "arguments": {"type": "object"},
                            },
                            "required": ["id", "kind", "description"],
                            "additionalProperties": False,
                        },
                    },
                    "repository_revision": string,
                },
                ["task", "actions"],
            )
        )
    if actions:
        definitions.extend(
            [
                (
                    "observe",
                    "Save a recoverable observation in the active working set.",
                    {
                        "type": {
                            "type": "string",
                            "enum": sorted(OBSERVATION_KINDS),
                        },
                        "summary": string,
                        "content": string,
                        "source": string,
                        "pinned": {"type": "boolean"},
                    },
                    ["type", "summary", "content"],
                ),
                (
                    "working_set",
                    "List compact active and deferred observation descriptors.",
                    {},
                    [],
                ),
                (
                    "recall",
                    "Restore and return an exact deferred observation by handle.",
                    {"handle": string},
                    ["handle"],
                ),
            ]
        )
    return [
        {
            "name": "context_" + name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": name not in {"next", "observe", "recall"}},
        }
        for name, description, properties, required in definitions
    ]


def dispatch(session: RepositoryContext, message: Any) -> dict[str, Any] | None:
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
    selection = bool(getattr(session, "selection_enabled", False))
    actions = bool(getattr(session, "action_enabled", False))
    if method == "initialize":
        version = params.get("protocolVersion")
        response["result"] = {
            "protocolVersion": version
            if isinstance(version, str) and version in PROTOCOLS
            else "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "contextlens", "version": "0.1.0"},
            "instructions": (
                "Use context_select for task evidence chosen by Jev through Vercel. "
                "Supply task and optional immediate focus. "
                if selection
                else "Use context_find when location is unknown. "
            )
            + "Read known paths directly. Handles check freshness; "
            "snapshots are historical.",
        }
    elif method == "ping":
        response["result"] = {}
    elif method == "tools/list":
        response["result"] = {
            "tools": tool_definitions(selection=selection, actions=actions)
        }
    elif method == "tools/call":
        definitions = {
            tool["name"]: tool
            for tool in tool_definitions(selection=selection, actions=actions)
        }
        name = params.get("name")
        args = params.get("arguments", {})
        try:
            if (
                not isinstance(name, str)
                or name not in definitions
                or not isinstance(args, dict)
            ):
                raise ValueError("unknown tool or invalid arguments")
            schema = definitions[name]["inputSchema"]
            if set(args) - set(schema["properties"]):
                raise ValueError("unknown tool argument")
            if set(schema["required"]) - set(args):
                raise ValueError("missing required argument")
            for key, value in args.items():
                expected = schema["properties"][key]
                if expected["type"] == "string" and not isinstance(value, str):
                    raise ValueError(f"{key} must be a string")
                if expected["type"] == "boolean" and not isinstance(value, bool):
                    raise ValueError(f"{key} must be a boolean")
                if expected["type"] == "integer" and (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < expected["minimum"]
                    or value > expected.get("maximum", 2**31)
                ):
                    raise ValueError(f"invalid {key}")
                if expected["type"] == "array" and (
                    not isinstance(value, list)
                    or len(value) < expected.get("minItems", 0)
                    or len(value) > expected.get("maxItems", 2**31)
                ):
                    raise ValueError(f"invalid {key}")
            result = session.call(name.removeprefix("context_"), args)
            response["result"] = {
                "content": [{"type": "text", "text": result}],
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
    session: RepositoryContext,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> None:
    while line := input_stream.readline(1024 * 1024 + 1):
        try:
            if len(line.encode("utf-8")) > 1024 * 1024:
                raise ValueError("request exceeds 1 MiB")
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
