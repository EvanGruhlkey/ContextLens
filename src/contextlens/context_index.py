"""Granular, exact-source discovery for on-demand repository context."""

from __future__ import annotations

import ast
import io
import re
import textwrap
import tokenize
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from contextlens.evidence import rank_units
from contextlens.evidence_index import RepositoryIndex, Unit, build_index

Declaration = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
SUPPORT_DEPTH_LIMIT = 4
SUPPORT_UNIT_LIMIT = 64


def _scope_declarations(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[set[str], set[str]]:
    global_names: set[str] = set()
    nonlocal_names: set[str] = set()
    queue: list[ast.AST] = list(node.body)
    while queue:
        current = queue.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(current, ast.Global):
            global_names.update(current.names)
        elif isinstance(current, ast.Nonlocal):
            nonlocal_names.update(current.names)
        queue.extend(ast.iter_child_nodes(current))
    return global_names, nonlocal_names


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
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            local = {
                argument.arg
                for argument in (
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                    *([node.args.vararg] if node.args.vararg else []),
                    *([node.args.kwarg] if node.args.kwarg else []),
                )
            }
            statements: list[ast.AST] = list(node.body)
            while statements:
                statement = statements.pop()
                if isinstance(
                    statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    local.add(statement.name)
                    continue
                if isinstance(statement, ast.Name) and isinstance(
                    statement.ctx, ast.Store
                ):
                    local.add(statement.id)
                statements.extend(ast.iter_child_nodes(statement))
            global_names, nonlocal_names = _scope_declarations(node)
            local.difference_update(global_names | nonlocal_names)
            # Assignment can itself depend on the prior external binding,
            # notably augmented assignments whose target has Store context.
            refs.update(global_names | nonlocal_names)
            refs = {
                ref
                for ref in refs
                if ref.split(".", 1)[0] not in local
                or ref.startswith(("self.", "cls."))
            }
            # Default values, decorators and annotations are evaluated outside
            # the function's parameter scope.
            signature: list[ast.AST] = [node.args, *node.decorator_list]
            if node.returns:
                signature.append(node.returns)
            refs.update(
                child.id
                for part in signature
                for child in ast.walk(part)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
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
                bindings = list(piece.bindings)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    bindings.extend(
                        argument.arg
                        for argument in (
                            *node.args.posonlyargs,
                            *node.args.args,
                            *node.args.kwonlyargs,
                            *([node.args.vararg] if node.args.vararg else []),
                            *([node.args.kwarg] if node.args.kwarg else []),
                        )
                    )
                return replace(piece, bindings=bindings, references=sorted(references))
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
            try:
                granular, headers = _python_units(path, source)
            except SyntaxError:
                # A malformed unrelated file must not prevent healthy matches.
                continue
        elif path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
            granular, headers = _javascript_units(path, source)
        else:
            continue
        additions = {unit.key: unit for unit in granular}
        known = {unit.key for unit in units}
        units = [
            replace(additions[unit.key], imports=unit.imports)
            if unit.key in additions
            else unit
            for unit in units
        ]
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
        fragments, missing = _support_closure(granular_index, unit, enclosing)
        result.append(
            Candidate(
                unit,
                fragments,
                score,
                index.hashes[unit.path],
                index.version,
                missing,
            )
        )
    return sorted(
        result, key=lambda item: (-item.score, item.unit.path, item.unit.start_line)
    )


def _overlaps(left: Unit, right: Unit) -> bool:
    return left.path == right.path and (
        left.start_line <= right.end_line and right.start_line <= left.end_line
    )


def _support_closure(
    index: RepositoryIndex,
    primary: Unit,
    enclosing: dict[str, tuple[Unit, ...]],
) -> tuple[tuple[Unit, ...], tuple[str, ...]]:
    """Bound dependency exploration and expose incomplete or ambiguous support."""
    fragments: dict[str, Unit] = {}
    unresolved: set[str] = set()
    queue = [(primary, 0)]
    visited: set[str] = set()
    while queue:
        current, depth = queue.pop(0)
        if current.key in visited:
            continue
        visited.add(current.key)
        references = set(current.references)
        for header in enclosing.get(current.key, ()):
            references.update(header.references)
            if not _overlaps(header, primary) and header.key not in fragments:
                if len(fragments) >= SUPPORT_UNIT_LIMIT:
                    unresolved.add("support_unit_limit_reached")
                    continue
                fragments[header.key] = header
                queue.append((header, depth))
        references.update(
            ref.split(".", 1)[1]
            for ref in tuple(references)
            if ref.startswith(("self.", "cls."))
        )
        if current.language == "python":
            try:
                parsed = ast.parse(textwrap.dedent(current.text))
            except SyntaxError:
                parsed = ast.Module(body=[], type_ignores=[])
            if parsed.body and isinstance(
                parsed.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                _, nonlocal_names = _scope_declarations(parsed.body[0])
                for name in nonlocal_names:
                    # A nonlocal refers to enclosing function scopes, never a
                    # same-named module binding. Enclosing exact fragments
                    # supply captured assignments and their own dependencies.
                    references.discard(name)
                    references = {
                        ref for ref in references if not ref.startswith(name + ".")
                    }
                    if not any(
                        name in piece.bindings
                        for piece in enclosing.get(current.key, ())
                    ):
                        unresolved.add(f"{name}: nonlocal_binding_unresolved")
        dependencies, missing = index.dependencies(
            replace(current, references=sorted(references))
        )
        unresolved.update(f"{item['symbol']}: {item['reason']}" for item in missing)
        # Lexical resolution is conservative: repeated bindings in distinct
        # scopes are not proof that any particular definition is the target.
        bindings: dict[tuple[str, str], list[Unit]] = {}
        for dependency in dependencies:
            for name in set(dependency.bindings) & references:
                bindings.setdefault((dependency.path, name), []).append(dependency)
        ambiguous = {
            name
            for (_, name), matches in bindings.items()
            if len({match.key for match in matches}) > 1
        }
        unresolved.update(f"{name}: ambiguous_binding" for name in ambiguous)
        for dependency in dependencies:
            if _overlaps(dependency, primary) or _overlaps(dependency, current):
                continue
            if dependency.key in fragments:
                continue
            if set(dependency.bindings) & ambiguous:
                continue
            if depth >= SUPPORT_DEPTH_LIMIT:
                unresolved.add(f"{dependency.key}: support_depth_limit_reached")
                continue
            if len(fragments) >= SUPPORT_UNIT_LIMIT:
                unresolved.add("support_unit_limit_reached")
                continue
            fragments[dependency.key] = dependency
            queue.append((dependency, depth + 1))
    return tuple(fragments.values()), tuple(sorted(unresolved))
