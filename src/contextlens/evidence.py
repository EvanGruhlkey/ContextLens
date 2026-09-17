"""Deterministic Python evidence retrieval without neural inference.

Select complete top-level units, expose omitted dependencies, and retain exact
source snapshots for expansion. Static name matching is deliberately conservative.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any

from contextlens.pruning import PruneRequest, ReceiptStore
from contextlens.pruning.model import estimate_tokens


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z0-9]*", text.lower()))


def retrieve_evidence(
    root: Path, task: str, receipts: ReceiptStore, *, budget: int = 2000
) -> dict[str, Any]:
    """Return ranked verbatim units under an approximate source-token budget.

    Budget covers retained source only; JSON metadata is excluded.
    Git enumeration respects ignored files. Non-Python files and parse failures
    are reported rather than silently treated as analyzed evidence.
    """
    if budget < 1 or not task.strip():
        raise ValueError("task must be nonempty and budget must be positive")
    root = root.resolve()
    try:
        command = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        raise ValueError("evidence retrieval requires a Git repository") from error
    query = _terms(task)
    units: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for name in sorted(set(command.stdout.decode("utf-8").split("\0")) - {""}):
        path = root / name
        if path.suffix != ".py":
            continue
        if not path.resolve().is_relative_to(root):
            skipped.append({"path": name, "reason": "outside_repository"})
            continue
        try:
            content = path.read_bytes().decode("utf-8")
            tree = ast.parse(content)
        except (OSError, UnicodeError, SyntaxError):
            skipped.append({"path": name, "reason": "unreadable_or_invalid_python"})
            continue
        lines = content.splitlines(keepends=True)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        for node in tree.body:
            decorators = getattr(node, "decorator_list", [])
            start = min([node.lineno, *[item.lineno for item in decorators]])
            end = node.end_lineno or node.lineno
            text = "".join(lines[start - 1 : end])
            names = {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}
            bindings = {
                item.id
                for item in ast.walk(node)
                if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
            }
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bindings = {node.name}
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                bindings = {
                    alias.asname or alias.name.split(".")[0] for alias in node.names
                }
            score = len(query & _terms(text)) + 2 * len(query & _terms(name))
            units.append(
                {
                    "path": name,
                    "start_line": start,
                    "end_line": end,
                    "text": text,
                    "content_hash": digest,
                    "score": score,
                    "tokens": estimate_tokens(text),
                    "names": names,
                    "bindings": bindings,
                    "source": content,
                }
            )
    units.sort(key=lambda unit: (-unit["score"], unit["path"], unit["start_line"]))
    selected: list[tuple[int, str]] = []
    used = 0
    for index, unit in enumerate(units):
        if unit["score"] and used + unit["tokens"] <= budget:
            selected.append((index, "lexical_match"))
            used += unit["tokens"]
    # Expand same-file definitions conservatively. Complete bodies are kept.
    pending = list(selected)
    seen = {index for index, _ in selected}
    unresolved: list[dict[str, Any]] = []
    while pending:
        index, _ = pending.pop()
        unit = units[index]
        for other_index, other in enumerate(units):
            if other_index in seen or other["path"] != unit["path"]:
                continue
            symbols = unit["names"] & other["bindings"]
            if not symbols:
                continue
            if used + other["tokens"] <= budget:
                seen.add(other_index)
                selected.append((other_index, "same_file_dependency"))
                pending.append((other_index, "same_file_dependency"))
                used += other["tokens"]
            else:
                unresolved.append(
                    {
                        "path": other["path"],
                        "start_line": other["start_line"],
                        "end_line": other["end_line"],
                        "symbols": sorted(symbols),
                        "reason": "dependency_exceeds_budget",
                    }
                )
    spans = []
    for index, reason in selected:
        unit = units[index]
        receipt = receipts.save(PruneRequest(task=task, content=unit["source"]))
        spans.append(
            {
                key: unit[key]
                for key in (
                    "path",
                    "start_line",
                    "end_line",
                    "text",
                    "content_hash",
                    "tokens",
                )
            }
            | {"reason": reason, "receipt_id": receipt.receipt_id}
        )
    omitted = [
        {key: unit[key] for key in ("path", "start_line", "end_line")}
        for index, unit in enumerate(units)
        if index not in seen
    ]
    return {
        "task": task,
        "repository": str(root),
        "spans": spans,
        "omitted": omitted,
        "unresolved_dependencies": unresolved,
        "skipped": skipped,
        "source_tokens": used,
        "source_budget": budget,
        "token_count_method": "estimated_utf8_bytes_div_4",
        "analysis_scope": "python_top_level_same_file_static_names",
        "semantic_completeness_verified": False,
    }
