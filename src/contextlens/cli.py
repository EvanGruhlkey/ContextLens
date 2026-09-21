"""Command line entry point: prune, compact, recover, and serve MCP."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from contextlens.compaction import CompactionConfig, compact_transcript
from contextlens.filtering import OutputPruner, PruneConfig, PruneRequest
from contextlens.jev import JevGateway
from contextlens.models import Message, ToolResult, ToolUse
from contextlens.receipts import ReceiptStore

DEFAULT_RECEIPTS = Path(".contextlens/receipts")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contextlens",
        description="Transparent context reduction for coding agents",
    )
    commands = parser.add_subparsers(dest="subcommand", required=True)

    prune = commands.add_parser(
        "prune", help="prune one large tool result before the coding model reads it"
    )
    prune.add_argument("--task", required=True)
    prune.add_argument("--focus", default="")
    prune.add_argument("--tool", default="tool")
    prune.add_argument("--path")
    prune.add_argument("--command")
    prune.add_argument(
        "--input", type=Path, help="tool output to read; stdin when omitted"
    )
    prune.add_argument("--receipts", type=Path, default=DEFAULT_RECEIPTS)
    prune.add_argument("--json", action="store_true")

    compact = commands.add_parser(
        "compact", help="drop stale tool calls and results from a transcript"
    )
    compact.add_argument(
        "--transcript", type=Path, help="JSON transcript; stdin when omitted"
    )
    compact.add_argument("--task", default="")
    compact.add_argument("--receipts", type=Path, default=DEFAULT_RECEIPTS)
    compact.add_argument("--trigger-tokens", type=int)
    compact.add_argument("--preserve-recent-messages", type=int)
    compact.add_argument("--json", action="store_true")

    recover = commands.add_parser(
        "recover", help="recover exact omitted content by receipt handle"
    )
    recover.add_argument("receipt_id")
    recover.add_argument("--start-line", type=int)
    recover.add_argument("--end-line", type=int)
    recover.add_argument("--receipts", type=Path, default=DEFAULT_RECEIPTS)

    mcp = commands.add_parser("mcp", help="serve context_prune and context_recover")
    mcp.add_argument("--task", default="")
    mcp.add_argument("--receipts", type=Path, default=DEFAULT_RECEIPTS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.subcommand == "prune":
            return _prune(arguments)
        if arguments.subcommand == "compact":
            return _compact(arguments)
        if arguments.subcommand == "recover":
            sys.stdout.write(
                ReceiptStore(arguments.receipts).read(
                    arguments.receipt_id,
                    start_line=arguments.start_line,
                    end_line=arguments.end_line,
                )
            )
            return 0
        from contextlens.mcp import serve_stdio

        serve_stdio(ReceiptStore(arguments.receipts), task=arguments.task)
        return 0
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        print(f"contextlens: {error}", file=sys.stderr)
        return 2


def _prune(arguments: argparse.Namespace) -> int:
    output = _read(arguments.input)
    tool_arguments: dict[str, Any] = {}
    if arguments.path:
        tool_arguments["path"] = arguments.path
    if arguments.command:
        tool_arguments["command"] = arguments.command
    outcome = OutputPruner(
        ReceiptStore(arguments.receipts),
        judge=JevGateway(),
        config=PruneConfig.from_env(),
    ).prune(
        PruneRequest(
            task=arguments.task,
            output=output,
            tool=arguments.tool,
            arguments=tool_arguments,
            focus=arguments.focus,
        )
    )
    if arguments.json:
        print(json.dumps({"text": outcome.text, **outcome.to_dict()}, indent=2))
    else:
        print(outcome.text)
    return 0


def _compact(arguments: argparse.Namespace) -> int:
    messages = parse_transcript(json.loads(_read(arguments.transcript)))
    overrides: dict[str, int] = {}
    if arguments.trigger_tokens is not None:
        overrides["trigger_tokens"] = arguments.trigger_tokens
    if arguments.preserve_recent_messages is not None:
        overrides["preserve_recent_messages"] = arguments.preserve_recent_messages
    result = compact_transcript(
        messages,
        JevGateway(),
        config=CompactionConfig(**overrides),
        receipts=ReceiptStore(arguments.receipts),
        task=arguments.task,
    )
    payload: dict[str, Any] = {
        "messages": render_transcript(result.messages),
        **result.to_dict(),
    }
    if arguments.json:
        print(json.dumps(payload, indent=2))
    else:
        print(json.dumps(payload["messages"], indent=2))
    return 0


def parse_transcript(value: Any) -> tuple[Message, ...]:
    """Read a transcript in the JSON shape ``render_transcript`` produces."""

    if not isinstance(value, list):
        raise ValueError("transcript must be a list of messages")
    messages: list[Message] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each transcript message must be an object")
        messages.append(
            Message(
                role=str(item.get("role", "")),
                text=str(item.get("text", "")),
                tool_uses=tuple(
                    ToolUse(
                        str(use["tool_use_id"]),
                        str(use["tool"]),
                        dict(use.get("arguments") or {}),
                    )
                    for use in item.get("tool_uses") or []
                ),
                tool_results=tuple(
                    ToolResult(
                        str(result["tool_use_id"]),
                        str(result.get("text", "")),
                        bool(result.get("is_error", False)),
                        str(result["receipt_id"])
                        if result.get("receipt_id")
                        else None,
                    )
                    for result in item.get("tool_results") or []
                ),
                pinned=bool(item.get("pinned", False)),
            )
        )
    return tuple(messages)


def render_transcript(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Serialize a transcript so it round-trips through ``parse_transcript``."""

    payload: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role, "text": message.text}
        if message.tool_uses:
            item["tool_uses"] = [
                {
                    "tool_use_id": use.tool_use_id,
                    "tool": use.tool,
                    "arguments": dict(use.arguments),
                }
                for use in message.tool_uses
            ]
        if message.tool_results:
            item["tool_results"] = [
                {
                    "tool_use_id": result.tool_use_id,
                    "text": result.text,
                    "is_error": result.is_error,
                    "receipt_id": result.receipt_id,
                }
                for result in message.tool_results
            ]
        if message.pinned:
            item["pinned"] = True
        payload.append(item)
    return payload


def _read(path: Path | None) -> str:
    return path.read_text(encoding="utf-8") if path is not None else sys.stdin.read()


if __name__ == "__main__":
    raise SystemExit(main())
