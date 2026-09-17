"""Granular, exact-source discovery for on-demand repository context."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, replace
from pathlib import Path

from contextlens.evidence import rank_units
from contextlens.evidence_index import Unit, build_index

Declaration = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


@dataclass(frozen=True)
class Candidate:
    unit: Unit
    support: tuple[Unit, ...]
    score: float
    content_hash: str
    repository_version: str
    unresolved: tuple[str, ...] = ()


def _python_units(
    path: str, source: str
) -> tuple[list[Unit], dict[str, tuple[Unit, ...]]]:
    """Keep declarations independently readable, without synthetic class stubs."""
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    units: list[Unit] = []
    support: dict[str, tuple[Unit, ...]] = {}

    def make(node: ast.stmt, end: int | None = None) -> Unit:
        start = min(
            [node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]]
        )
        finish = end or node.end_lineno or node.lineno
        refs = {
            item.id
            for item in ast.walk(node)
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
        }
        refs.update(
            f"{item.value.id}.{item.attr}"
            for item in ast.walk(node)
            if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name)
        )
        bindings = (
            [node.name]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            else sorted(
                {
                    item.id
                    for item in ast.walk(node)
                    if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
                }
            )
        )
        return Unit(
            path,
            start,
            finish,
            "".join(lines[start - 1 : finish]),
            bindings,
            sorted(refs),
            [],
            "python",
        )

    def header(node: Declaration) -> Unit:
        # Locate the declaration colon, ignoring colons inside annotations.
        fragment = "".join(lines[node.lineno - 1 : node.end_lineno])
        depth = 0
        for token in tokenize.generate_tokens(io.StringIO(fragment).readline):
            if token.type != tokenize.OP:
                continue
            if token.string in "([{":
                depth += 1
            elif token.string in ")]}":
                depth -= 1
            elif token.string == ":" and depth == 0:
                piece = make(node, node.lineno + token.end[0] - 1)
                signature: list[ast.AST] = list(node.decorator_list)
                if isinstance(node, ast.ClassDef):
                    signature.extend(node.bases)
                    signature.extend(node.keywords)
                else:
                    signature.append(node.args)
                    if node.returns:
                        signature.append(node.returns)
                references = {
                    child.id
                    for part in signature
                    for child in ast.walk(part)
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
                }
                return replace(piece, references=sorted(references))
        return make(node)

    def visit(node: ast.AST, ancestors: tuple[Declaration, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            unit = make(node)
            units.append(unit)
            needed = set(unit.references)
            fragments: dict[str, Unit] = {}
            for ancestor in ancestors:
                piece = header(ancestor)
                fragments[piece.key] = piece
                for statement in ancestor.body:
                    if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                        continue
                    field = make(statement)
                    names = set(field.bindings)
                    if names & needed or any(
                        f"{receiver}.{name}" in needed
                        for name in names
                        for receiver in ("self", "cls")
                    ):
                        fragments[field.key] = field
            support[unit.key] = tuple(fragments.values())
            ancestors = (*ancestors, node)
        for child in ast.iter_child_nodes(node):
            visit(child, ancestors)

    visit(tree, ())
    return units, support


def discover_candidates(
    root: Path, state: Path, query: str, focus: str = ""
) -> list[Candidate]:
    """Rank exact code units; abstain when no indexed evidence matches."""
    if not query.strip() and not focus.strip():
        return []
    index = build_index(root, state / "index.sqlite")
    units = list(index.units)
    enclosing: dict[str, tuple[Unit, ...]] = {}
    for path, source in index.sources.items():
        if not path.endswith(".py"):
            continue
        granular, headers = _python_units(path, source)
        known = {unit.key for unit in units}
        units.extend(unit for unit in granular if unit.key not in known)
        enclosing.update(headers)
    granular_index = replace(index, units=units)
    exact = set(re.findall(r"[\w./-]+", query + " " + focus))
    result = []
    for unit, score in rank_units(granular_index, query, focus):
        if score <= 0:
            continue
        score += 20 * len(exact & set(unit.bindings))
        score += 10 * int(unit.path in exact)
        fragments = {piece.key: piece for piece in enclosing.get(unit.key, ())}
        references = set(unit.references)
        for piece in fragments.values():
            references.update(piece.references)
        references.update(
            ref.split(".", 1)[1]
            for ref in tuple(references)
            if ref.startswith(("self.", "cls."))
        )
        dependencies, missing = granular_index.dependencies(
            replace(unit, references=sorted(references))
        )
        for dependency in dependencies:
            # Never expand a method back into its complete enclosing class.
            if dependency.path == unit.path and (
                dependency.start_line <= unit.start_line
                and dependency.end_line >= unit.end_line
                or unit.start_line <= dependency.start_line
                and unit.end_line >= dependency.end_line
            ):
                continue
            fragments[dependency.key] = dependency
        result.append(
            Candidate(
                unit,
                tuple(fragments.values()),
                score,
                index.hashes[unit.path],
                index.version,
                tuple(f"{item['symbol']}: {item['reason']}" for item in missing),
            )
        )
    return sorted(
        result, key=lambda item: (-item.score, item.unit.path, item.unit.start_line)
    )
