"""Agent-facing evidence tools, external memory, and per-call accounting."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from contextlens.evidence import retrieve_evidence, verify_source
from contextlens.evidence_index import build_index
from contextlens.pruning import (
    ContextPruner,
    PruneRequest,
    ReceiptStore,
    SemanticScorer,
)
from contextlens.pruning.model import ObservationKind, estimate_tokens


def tokenizer(encoding: str = "estimate") -> tuple[Callable[[str], int], str]:
    if encoding == "estimate":
        return estimate_tokens, "estimated_utf8_bytes_div_4"
    try:
        import tiktoken
    except ImportError as error:
        raise ValueError(
            "install contextlens[evidence] to use exact token counting"
        ) from error
    codec = tiktoken.get_encoding(encoding)
    return lambda text: len(codec.encode(text, disallowed_special=())), encoding


class EvidenceSession:
    """A root-confined local tool boundary; never writes source or runs commands.

    Working memory is an explicit bounded view, not a rewrite of the hosted
    model's conversation. The caller controls its own compaction policy.
    """

    def __init__(
        self,
        root: Path,
        state: Path,
        *,
        encoding: str = "estimate",
        policy: str = "dependency",
        scorer: SemanticScorer | None = None,
    ) -> None:
        self.root = root.resolve()
        self.state = state.resolve()
        self.state.mkdir(parents=True, exist_ok=True)
        self.receipts = ReceiptStore(self.state / "receipts")
        self.count, self.count_method = tokenizer(encoding)
        self.policy = policy
        self.scorer = scorer
        self.calls: list[dict[str, Any]] = []
        self.memory: list[dict[str, Any]] = []
        self.returned_ranges: set[tuple[str, int, int]] = set()
        memory = self.state / "memory.json"
        if memory.exists():
            self.memory = json.loads(memory.read_text(encoding="utf-8"))

    def call(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        error = None
        result: dict[str, Any] = {}
        try:
            result = self._call(operation, arguments)
            return result
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            error = str(exc)
            result = {"error": error}
            raise
        finally:
            record = {
                "operation": operation,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "request_tokens": self.count(json.dumps(dict(arguments))),
                "response_tokens": self.count(json.dumps(result, ensure_ascii=False)),
                "token_count_method": self.count_method,
                "error": error,
                "snapshot_recovery": operation == "expand",
            }
            self.calls.append(record)
            with (self.state / "tool-calls.jsonl").open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write(json.dumps(record) + "\n")

    def _call(self, operation: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "retrieve":
            task = _string(args, "task")
            index = build_index(self.root, self.state / "index.sqlite")
            return retrieve_evidence(
                self.root,
                task,
                self.receipts,
                budget=_integer(args, "budget", 3000),
                focus=_string(args, "focus", ""),
                index=index,
                policy=self.policy,
                token_counter=self.count,
                token_count_method=self.count_method,
                response_budget=_optional_integer(args, "response_budget"),
            )
        if operation == "read":
            path = _string(args, "path")
            target = (self.root / path).resolve()
            if not target.is_relative_to(self.root):
                raise ValueError("source path is outside the repository")
            if target.stat().st_size > 1024 * 1024:
                raise ValueError("source exceeds 1 MiB; use a smaller tool observation")
            expected = _string(args, "expected_hash", "")
            if expected:
                verify_source(self.root, path, expected)
            source = target.read_bytes().decode("utf-8")
            receipt = self.receipts.save(
                PruneRequest(task="Read source", content=source)
            )
            start = _integer(args, "start_line", 1)
            end = _integer(args, "end_line", len(source.splitlines()))
            content = self.receipts.read(
                receipt.receipt_id, start_line=start, end_line=end
            )
            budget = _integer(args, "budget", 8000)
            if budget < 1:
                raise ValueError("budget must be positive")
            if self.count(content) > budget:
                return {
                    "path": path,
                    "receipt_id": receipt.receipt_id,
                    "content_hash": receipt.content_hash,
                    "line_count": receipt.line_count,
                    "status": "range_exceeds_budget_expand_smaller_range",
                    "content": "",
                    "source_tokens": self.count(content),
                }
            range_key = (receipt.content_hash, start, end)
            deduplicate = args.get("deduplicate", False)
            if not isinstance(deduplicate, bool):
                raise ValueError("deduplicate must be a boolean")
            if deduplicate and range_key in self.returned_ranges:
                return {
                    "path": path,
                    "receipt_id": receipt.receipt_id,
                    "content_hash": receipt.content_hash,
                    "start_line": start,
                    "end_line": end,
                    "content": "",
                    "status": "already_returned_expand_if_needed",
                }
            self.returned_ranges.add(range_key)
            return {
                "path": path,
                "receipt_id": receipt.receipt_id,
                "content_hash": receipt.content_hash,
                "start_line": start,
                "end_line": end,
                "content": content,
                "status": "exact_source",
            }
        if operation == "expand":
            receipt_id = _string(args, "receipt_id")
            expand_start = _optional_integer(args, "start_line")
            expand_end = _optional_integer(args, "end_line")
            content = self.receipts.read(
                receipt_id, start_line=expand_start, end_line=expand_end
            )
            if self.count(content) > _integer(args, "budget", 8000):
                raise ValueError(
                    "expansion exceeds budget; request a smaller line range"
                )
            return {
                "receipt_id": receipt_id,
                "content": content,
                "content_hash": self.receipts.metadata(receipt_id).content_hash,
                "snapshot_only": True,
                "current_source_verified": False,
            }
        if operation == "verify":
            path = _string(args, "path")
            verify_source(self.root, path, _string(args, "expected_hash"))
            return {"path": path, "current_source_verified": True}
        if operation == "remember":
            content = _string(args, "content")
            label = _string(args, "label", "observation")
            receipt = self.receipts.save(
                PruneRequest(
                    task="Remember observation",
                    content=content,
                    kind=ObservationKind.TEXT,
                )
            )
            item: dict[str, Any] = {
                "label": label,
                "receipt_id": receipt.receipt_id,
                "tokens": self.count(content),
                "pinned": bool(args.get("pinned", False)),
            }
            self.memory.append(item)
            (self.state / "memory.json").write_text(
                json.dumps(self.memory), encoding="utf-8"
            )
            return {"receipt_id": receipt.receipt_id, "stored": True}
        if operation == "view":
            budget = _integer(args, "budget", 2000)
            if budget < 1:
                raise ValueError("budget must be positive")
            kept: list[dict[str, Any]] = []
            deferred: list[dict[str, Any]] = []
            used = 0
            ordered = sorted(
                enumerate(self.memory),
                key=lambda pair: (not pair[1]["pinned"], -pair[0]),
            )
            for _, item in ordered:
                expanded = dict(item)
                expanded["content"] = self.receipts.read(item["receipt_id"])
                count = self.count(json.dumps(expanded))
                if used + count <= budget:
                    kept.append(expanded)
                    used += count
                else:
                    deferred.append(
                        {"label": item["label"], "receipt_id": item["receipt_id"]}
                    )
            return {
                "working_memory": kept,
                "deferred": deferred[:20],
                "deferred_count": len(deferred),
                "retained_item_tokens": used,
                "hosted_conversation_modified": False,
            }
        if operation == "observe":
            content = _string(args, "content")
            task = _string(args, "task")
            request = PruneRequest(
                task=task,
                content=content,
                focus=_string(args, "focus", "") or None,
                kind=ObservationKind(_string(args, "kind", "code")),
                language=_string(args, "language", "python"),
            )
            if self.scorer is None:
                receipt = self.receipts.save(request)
                return {
                    "content": content,
                    "receipt_id": receipt.receipt_id,
                    "status": "neural_backend_not_configured_full_observation",
                }
            return (
                ContextPruner(self.scorer, self.receipts, token_counter=self.count)
                .prune(request)
                .to_dict()
            )
        raise ValueError(f"unknown evidence operation: {operation}")


def _string(args: Mapping[str, Any], key: str, default: str | None = None) -> str:
    value = args.get(key, default)
    if not isinstance(value, str) or (default is None and not value.strip()):
        raise ValueError(f"{key} must be a nonempty string")
    return value


def _integer(args: Mapping[str, Any], key: str, default: int) -> int:
    value = args.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_integer(args: Mapping[str, Any], key: str) -> int | None:
    return None if args.get(key) is None else _integer(args, key, 0)
