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
    DEFAULT_SWE_PRUNER_MODEL,
    ContextPruner,
    HttpSemanticScorer,
    LocalSwePrunerScorer,
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

    find = commands.add_parser("find", help="find repository locations on demand")
    find.add_argument("--query", required=True)
    find.add_argument("--focus", default="")
    find.add_argument("--root", type=Path, default=Path.cwd())
    find.add_argument("--state", type=Path, default=Path(".contextlens"))
    find.add_argument("--encoding", default="estimate")
    find.add_argument("--budget", type=int, default=1200)
    find.add_argument("--limit", type=int, default=5)

    read = commands.add_parser("read", help="read compact exact current evidence")
    read.add_argument("--path")
    read.add_argument("--handle")
    read.add_argument("--start-line", type=int)
    read.add_argument("--end-line", type=int)
    read.add_argument("--root", type=Path, default=Path.cwd())
    read.add_argument("--state", type=Path, default=Path(".contextlens"))
    read.add_argument("--encoding", default="estimate")
    read.add_argument("--budget", type=int, default=3000)
    read.add_argument("--snapshot", action="store_true")

    retrieve = commands.add_parser(
        "retrieve", help="retrieve versioned source evidence without model inference"
    )
    retrieve.add_argument("--task", required=True)
    retrieve.add_argument("--root", type=Path, default=Path.cwd())
    retrieve.add_argument("--budget", type=int)
    retrieve.add_argument("--focus", default="")
    retrieve.add_argument("--response-budget", type=int)
    retrieve.add_argument("--encoding", default="estimate")
    retrieve.add_argument(
        "--policy", choices=("dependency", "lexical", "full"), default="full"
    )
    retrieve.add_argument(
        "--receipts", type=Path, default=Path(".contextlens/receipts")
    )

    mcp = commands.add_parser(
        "mcp", help="serve root-confined evidence tools over stdio"
    )
    mcp.add_argument("--root", type=Path, default=Path.cwd())
    mcp.add_argument("--state", type=Path, default=Path(".contextlens"))
    mcp.add_argument("--encoding", default="estimate")
    mcp.add_argument("--profile", choices=("compact", "legacy"), default="compact")
    mcp.add_argument("--backend", choices=("none", "local", "http"), default="none")
    mcp.add_argument("--backend-url", default="http://127.0.0.1:8000/prune")
    mcp.add_argument("--model", default=DEFAULT_SWE_PRUNER_MODEL)
    mcp.add_argument("--allow-cpu", action="store_true")
    mcp.add_argument("--checkpoint-namespace")
    mcp.add_argument(
        "--policy", choices=("dependency", "lexical", "full"), default="full"
    )

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
        "--backend",
        choices=("local", "http"),
        default="local",
        help="run the 0.6B model locally (default) or call a model server",
    )
    prune.add_argument(
        "--model",
        default=os.environ.get(
            "CONTEXTLENS_MODEL",
            os.environ.get("SWEPRUNER_MODEL_PATH", DEFAULT_SWE_PRUNER_MODEL),
        ),
        help="Hugging Face model ID or local checkpoint path",
    )
    prune.add_argument(
        "--allow-cpu",
        action="store_true",
        help="allow slow, memory-intensive CPU model inference",
    )
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
    serve.add_argument("--backend", choices=("local", "http"), default="local")
    serve.add_argument(
        "--model",
        default=os.environ.get(
            "CONTEXTLENS_MODEL",
            os.environ.get("SWEPRUNER_MODEL_PATH", DEFAULT_SWE_PRUNER_MODEL),
        ),
    )
    serve.add_argument("--allow-cpu", action="store_true")
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
        if arguments.command in {"find", "read"}:
            from contextlens.context_tools import RepositoryContext

            context = RepositoryContext(
                arguments.root, arguments.state, encoding=arguments.encoding
            )
            if arguments.command == "find":
                payload = {
                    "query": arguments.query,
                    "focus": arguments.focus,
                    "limit": arguments.limit,
                    "budget": arguments.budget,
                }
                operation = "find"
            else:
                payload = {
                    key: value
                    for key, value in vars(arguments).items()
                    if key in {"path", "handle", "start_line", "end_line", "budget"}
                    and value is not None
                }
                operation = "expand" if arguments.snapshot else "read"
            print(context.call(operation, payload))
            return 0
        if arguments.command == "mcp":
            if arguments.profile == "compact":
                from contextlens.context_mcp import serve_stdio as serve_context
                from contextlens.context_tools import RepositoryContext

                if arguments.backend != "none":
                    raise ValueError("neural backends require --profile legacy")
                serve_context(
                    RepositoryContext(
                        arguments.root, arguments.state, encoding=arguments.encoding
                    )
                )
                return 0
            from contextlens.evidence_mcp import serve_stdio
            from contextlens.evidence_session import EvidenceSession

            active_scorer = None
            if arguments.backend != "none":
                active_scorer = _configured_scorer(arguments)
                if arguments.checkpoint_namespace:
                    from contextlens.pruning.cache import CachedSemanticScorer

                    active_scorer = CachedSemanticScorer(
                        active_scorer, arguments.checkpoint_namespace
                    )

            serve_stdio(
                EvidenceSession(
                    arguments.root,
                    arguments.state,
                    encoding=arguments.encoding,
                    policy=arguments.policy,
                    scorer=active_scorer,
                )
            )
            return 0
        if arguments.command == "retrieve":
            from contextlens.evidence import retrieve_evidence
            from contextlens.evidence_session import tokenizer

            counter, method = tokenizer(arguments.encoding)

            result = retrieve_evidence(
                arguments.root,
                arguments.task,
                ReceiptStore(arguments.receipts),
                budget=arguments.budget
                if arguments.budget is not None
                else (30000 if arguments.policy == "full" else 3000),
                focus=arguments.focus,
                response_budget=arguments.response_budget,
                policy=arguments.policy,
                token_counter=counter,
                token_count_method=method,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if arguments.command == "prune":
            return _prune(arguments, scorer)
        if arguments.command == "recover":
            return _recover(arguments)
        from contextlens.pruning.server import serve

        serve(
            host=arguments.host,
            port=arguments.port,
            receipts=arguments.receipts,
            scorer=_configured_scorer(arguments),
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
    tool_arguments = _parse_arguments(arguments.argument)
    if arguments.input is not None:
        tool_arguments.setdefault("path", str(arguments.input))
    request = PruneRequest(
        task=arguments.task,
        focus=arguments.focus,
        content=content,
        tool=arguments.tool or ("read_file" if arguments.input is not None else None),
        arguments=tool_arguments,
        kind=ObservationKind(arguments.kind),
        language=arguments.language,
        threshold=arguments.threshold,
        minimum_tokens=arguments.minimum_tokens,
        dependency_hops=arguments.dependency_hops,
        context_radius=arguments.context_radius,
    )
    active_scorer = scorer or _configured_scorer(arguments)
    result = ContextPruner(
        active_scorer,
        ReceiptStore(arguments.receipts),
    ).prune(request)
    if arguments.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(result.text)
    return 0


def _configured_scorer(arguments: argparse.Namespace) -> SemanticScorer:
    if arguments.backend == "http":
        return HttpSemanticScorer(arguments.backend_url)
    return LocalSwePrunerScorer(
        arguments.model,
        allow_cpu=arguments.allow_cpu,
    )


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
