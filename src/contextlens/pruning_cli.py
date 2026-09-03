"""Command line interface for task-conditioned observation pruning."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from contextlens.pruning import (
    ContextPruner,
    HttpSemanticScorer,
    ObservationKind,
    PruneRequest,
    ReceiptStore,
    SemanticScorer,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contextlens",
        description="Task-conditioned, structure-aware observation pruning",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prune = commands.add_parser("prune", help="prune one source observation")
    prune.add_argument("--task", required=True)
    prune.add_argument("--focus")
    prune.add_argument("--input", type=Path, help="source path; stdin when omitted")
    prune.add_argument("--tool")
    prune.add_argument("--argument", action="append", default=[])
    prune.add_argument("--kind", choices=tuple(ObservationKind), default="code")
    prune.add_argument("--language", default="python")
    prune.add_argument("--threshold", type=float, default=0.5)
    prune.add_argument("--minimum-tokens", type=int, default=256)
    prune.add_argument("--dependency-hops", type=int, default=2)
    prune.add_argument("--context-radius", type=int, default=1)
    prune.add_argument(
        "--backend-url",
        default=os.environ.get(
            "CONTEXTLENS_BACKEND_URL",
            "http://127.0.0.1:8000/prune",
        ),
    )
    prune.add_argument(
        "--receipts",
        type=Path,
        default=Path(".contextlens/receipts"),
    )
    prune.add_argument("--json", action="store_true")

    recover = commands.add_parser("recover", help="recover an original observation")
    recover.add_argument("receipt_id")
    recover.add_argument("--start-line", type=int)
    recover.add_argument("--end-line", type=int)
    recover.add_argument(
        "--receipts",
        type=Path,
        default=Path(".contextlens/receipts"),
    )

    serve = commands.add_parser("serve", help="run the local pruning service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument(
        "--backend-url",
        default=os.environ.get(
            "CONTEXTLENS_BACKEND_URL",
            "http://127.0.0.1:8000/prune",
        ),
    )
    serve.add_argument(
        "--receipts",
        type=Path,
        default=Path(".contextlens/receipts"),
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    scorer: SemanticScorer | None = None,
) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "prune":
            return _prune(arguments, scorer)
        if arguments.command == "recover":
            return _recover(arguments)
        from contextlens.pruning.server import serve

        serve(
            host=arguments.host,
            port=arguments.port,
            backend_url=arguments.backend_url,
            receipts=arguments.receipts,
        )
        return 0
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        print(f"contextlens: {error}", file=sys.stderr)
        return 2


def _prune(arguments: argparse.Namespace, scorer: SemanticScorer | None) -> int:
    content = (
        arguments.input.read_text(encoding="utf-8")
        if arguments.input is not None
        else sys.stdin.read()
    )
    request = PruneRequest(
        task=arguments.task,
        focus=arguments.focus,
        content=content,
        tool=arguments.tool,
        arguments=_parse_arguments(arguments.argument),
        kind=ObservationKind(arguments.kind),
        language=arguments.language,
        threshold=arguments.threshold,
        minimum_tokens=arguments.minimum_tokens,
        dependency_hops=arguments.dependency_hops,
        context_radius=arguments.context_radius,
    )
    active_scorer = scorer or HttpSemanticScorer(arguments.backend_url)
    result = ContextPruner(
        active_scorer,
        ReceiptStore(arguments.receipts),
    ).prune(request)
    if arguments.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(result.text)
    return 0


def _recover(arguments: argparse.Namespace) -> int:
    content = ReceiptStore(arguments.receipts).read(
        arguments.receipt_id,
        start_line=arguments.start_line,
        end_line=arguments.end_line,
    )
    sys.stdout.write(content)
    return 0


def _parse_arguments(items: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"tool argument must use key=value: {item}")
        key, value = item.split("=", 1)
        if not key:
            raise ValueError("tool argument key cannot be empty")
        result[key] = value
    return result


if __name__ == "__main__":
    raise SystemExit(main())
