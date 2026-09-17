"""Granular, exact-source discovery for on-demand repository context."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

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


def _javascript_units(
    path: str, source: str
) -> tuple[list[Unit], dict[str, tuple[Unit, ...]]]:
    """Use the optional parser to expose nested JS/TS declarations safely."""
    try:
        from tree_sitter import Language, Parser

        if path.endswith((".ts", ".tsx")):
            import tree_sitter_typescript as grammar

            capsule = (
                grammar.language_tsx()
                if path.endswith(".tsx")
                else grammar.language_typescript()
            )
        else:
            import tree_sitter_javascript as js_grammar

            capsule = js_grammar.language()
    except ImportError:
        return [], {}
    data = source.encode()
    tree = Parser(Language(capsule)).parse(data)
    if tree.root_node.has_error:
        return [], {}
    lines = source.splitlines(keepends=True)
    language = "typescript" if path.endswith((".ts", ".tsx")) else "javascript"
    units: list[Unit] = []
    support: dict[str, tuple[Unit, ...]] = {}
    declarations = {
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
        "class_declaration",
        "function_expression",
        "arrow_function",
    }

    def text(node: Any) -> str:
        return data[node.start_byte : node.end_byte].decode()

    def make(node: Any, end: int | None = None) -> Unit:
        start = node.start_point.row + 1
        finish = end or node.end_point.row + 1
        name = node.child_by_field_name("name")
        if not name and node.parent and node.parent.type == "variable_declarator":
            name = node.parent.child_by_field_name("name")
        refs: set[str] = set()
        queue = [node]
        while queue:
            child = queue.pop()
            queue.extend(child.named_children)
            if child.type in {"identifier", "property_identifier", "type_identifier"}:
                refs.add(text(child))
        return Unit(
            path,
            start,
            finish,
            "".join(lines[start - 1 : finish]),
            [text(name)] if name else [],
            sorted(refs),
            [],
            language,
        )

    def visit(node: Any, ancestors: tuple[Any, ...]) -> None:
        if node.type in declarations:
            unit = make(node)
            units.append(unit)
            fragments: dict[str, Unit] = {}
            for ancestor in ancestors:
                body = ancestor.child_by_field_name("body")
                if not body:
                    continue
                header = make(ancestor, body.start_point.row + 1)
                # All fragments remain complete, original source lines.
                fragments[header.key] = replace(
                    header,
                    references=re.findall(r"[A-Za-z_$][\w$]*", header.text),
                )
                for field in body.named_children:
                    if field.type not in {
                        "public_field_definition",
                        "field_definition",
                        "lexical_declaration",
                        "variable_declaration",
                    }:
                        continue
                    field_names = [
                        field.child_by_field_name("name")
                        or field.child_by_field_name("property")
                    ]
                    field_names.extend(
                        child.child_by_field_name("name")
                        for child in field.named_children
                        if child.type == "variable_declarator"
                    )
                    if any(
                        name and text(name) in unit.references for name in field_names
                    ):
                        piece = make(field)
                        fragments[piece.key] = piece
            support[unit.key] = tuple(fragments.values())
            ancestors = (*ancestors, node)
        for child in node.named_children:
            visit(child, ancestors)

    visit(tree.root_node, ())
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
        if path.endswith(".py"):
            granular, headers = _python_units(path, source)
        elif path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
            granular, headers = _javascript_units(path, source)
        else:
            continue
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
