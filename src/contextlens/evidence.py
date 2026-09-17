"""Budgeted, versioned evidence selection and dependency-aware expansion."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from contextlens.evidence_index import RepositoryIndex, Unit, build_index, terms
from contextlens.pruning import PruneRequest, ReceiptStore
from contextlens.pruning.model import estimate_tokens

STOP_WORDS = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "and",
    "in",
    "is",
    "for",
    "with",
    "it",
    "that",
    "when",
    "be",
    "as",
    "by",
    "on",
    "make",
    "fix",
}


def rank_units(
    index: RepositoryIndex, task: str, focus: str = ""
) -> list[tuple[Unit, float]]:
    """BM25-style lexical ranking with path and exact symbol boosts."""
    query = set(terms(task + " " + focus)) - STOP_WORDS
    documents = [Counter(terms(u.text)) for u in index.units]
    average = sum(sum(doc.values()) for doc in documents) / max(len(documents), 1)
    frequencies = Counter(term for doc in documents for term in doc)
    ranked = []
    for unit, doc in zip(index.units, documents, strict=True):
        length = sum(doc.values())
        score = 0.0
        for term in query:
            count = doc[term]
            weight = math.log(
                1
                + (len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
            )
            denominator = count + 1.2 * (0.25 + 0.75 * length / max(average, 1))
            score += weight * count * 2.2 / denominator
        score += 2 * len(query & set(terms(" ".join(unit.bindings))))
        score += 0.5 * len(query & set(terms(unit.path)))
        ranked.append((unit, score))
    return sorted(ranked, key=lambda row: (-row[1], row[0].path, row[0].start_line))


def retrieve_evidence(
    root: Path,
    task: str,
    receipts: ReceiptStore,
    *,
    budget: int = 2000,
    focus: str = "",
    token_counter: Callable[[str], int] = estimate_tokens,
    token_count_method: str = "estimated_utf8_bytes_div_4",
    response_budget: int | None = None,
    policy: str = "dependency",
    index: RepositoryIndex | None = None,
) -> dict[str, Any]:
    """Reserve dependencies before lower-ranked matches; never rewrite source."""
    if (
        budget < 1
        or not task.strip()
        or (response_budget is not None and response_budget < 1)
    ):
        raise ValueError("task must be nonempty and budgets must be positive")
    if policy not in {"dependency", "lexical", "full"}:
        raise ValueError("unknown evidence policy")
    started = time.perf_counter()
    index = index or build_index(root, receipts.root.parent / "index.sqlite")
    if index.root != root.resolve():
        raise ValueError("index belongs to a different repository")
    snapshots = {
        path: receipts.save(PruneRequest(task=task, content=source)).receipt_id
        for path, source in index.sources.items()
    }
    ranked = rank_units(index, task, focus)
    selected: dict[str, tuple[Unit, str]] = {}
    unresolved: list[dict[str, Any]] = []
    used = 0
    if policy == "full":
        file_scores: dict[str, float] = {}
        for unit, score in ranked:
            file_scores[unit.path] = max(file_scores.get(unit.path, 0), score)
        candidates = [
            Unit(path, 1, len(source.splitlines()), source, [], [], [], "text", True)
            for path, source in index.sources.items()
            if file_scores.get(path, 0) > 0
        ]
        ranked = sorted(
            [(u, file_scores[u.path]) for u in candidates],
            key=lambda row: (-row[1], row[0].path),
        )[:3]
    for seed, score in ranked:
        if not score or seed.key in selected:
            continue
        pending = [(seed, "lexical_match" if policy != "full" else "full_file")]
        visited: set[str] = set()
        while pending:
            unit, reason = pending.pop(0)
            if unit.key in selected or unit.key in visited:
                continue
            visited.add(unit.key)
            count = token_counter(unit.text)
            if used + count > budget:
                if reason == "dependency":
                    unresolved.append(
                        {
                            "path": unit.path,
                            "start_line": unit.start_line,
                            "end_line": unit.end_line,
                            "symbols": unit.bindings,
                            "receipt_id": snapshots[unit.path],
                            "reason": "dependency_exceeds_budget",
                        }
                    )
                continue
            selected[unit.key] = (unit, reason)
            used += count
            if policy == "dependency":
                dependencies, missing = index.dependencies(unit)
                unresolved.extend(missing)
                pending.extend((dep, "dependency") for dep in dependencies)
    spans = [
        {
            "path": unit.path,
            "start_line": unit.start_line,
            "end_line": unit.end_line,
            "text": unit.text,
            "content_hash": index.hashes[unit.path],
            "tokens": token_counter(unit.text),
            "reason": reason,
            "language": unit.language,
            "fallback": unit.fallback,
            "receipt_id": snapshots[unit.path],
        }
        for unit, reason in selected.values()
    ]
    spans.sort(key=lambda span: (span["path"], span["start_line"]))
    omitted = [
        {
            "path": u.path,
            "start_line": u.start_line,
            "end_line": u.end_line,
            "receipt_id": snapshots[u.path],
        }
        for u in index.units
        if u.key not in selected and policy != "full"
    ]
    unresolved = list(
        {json.dumps(item, sort_keys=True): item for item in unresolved}.values()
    )
    bundle: dict[str, Any] = {
        "schema_version": "2.0",
        "task": task,
        "focus": focus,
        "repository": str(index.root),
        "repository_version": index.version,
        "commit": index.commit,
        "policy": policy,
        "spans": spans,
        "omitted": omitted[:20],
        "omitted_count": len(omitted),
        "unresolved_dependencies": unresolved[:20],
        "unresolved_dependency_count": len(unresolved),
        "skipped": index.skipped[:20],
        "skipped_count": len(index.skipped),
        "source_tokens": used,
        "source_budget": budget,
        "token_count_method": token_count_method,
        "cache_hits": index.cache_hits,
        "analysis_scope": "static_python_js_ts_imports_with_text_fallback",
        "semantic_completeness_verified": False,
        "status": "selected" if spans else "no_matching_unit_fits",
        "response_budget_omission_count": 0,
    }
    if response_budget is not None:
        bundle["omitted"] = []
        while (
            bundle["spans"]
            and token_counter(json.dumps(bundle, ensure_ascii=False)) + 64
            > response_budget
        ):
            removed = bundle["spans"].pop()
            bundle["source_tokens"] -= removed["tokens"]
            bundle["response_budget_omission_count"] += 1
            bundle["omitted_count"] += 1
            bundle["status"] = "response_budget_limited_expand_required"
        if token_counter(json.dumps(bundle, ensure_ascii=False)) + 64 > response_budget:
            raise ValueError(
                "response budget cannot fit evidence metadata; increase it"
            )
    bundle["selection_ms"] = round((time.perf_counter() - started) * 1000, 3)
    bundle["response_tokens"] = 0
    for _ in range(3):
        bundle["response_tokens"] = token_counter(
            json.dumps(bundle, ensure_ascii=False)
        )
    if response_budget is not None and bundle["response_tokens"] > response_budget:
        raise ValueError("response budget cannot fit accounting metadata; increase it")
    return bundle


def verify_source(root: Path, path: str, expected_hash: str) -> None:
    """Reject stale or escaped source before edits or live expansion."""
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("source path is outside the repository")
    if hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash:
        raise ValueError("source changed; retrieve fresh evidence before editing")
