"""Structural support recovery for source observations."""

from __future__ import annotations

import ast
import re
from collections import defaultdict, deque
from dataclasses import dataclass

from contextlens.pruning.model import LineReason

_SCOPES = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_COMPOUNDS = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Match,
)
_SIMPLE_STATEMENTS = (
    ast.Import,
    ast.ImportFrom,
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.Return,
    ast.Raise,
    ast.Assert,
    ast.Expr,
)


@dataclass(frozen=True, slots=True)
class StructuralResult:
    """Support lines recovered from semantic evidence."""

    reasons: dict[int, tuple[LineReason, ...]]
    parse_error: str | None = None


def close_python_dependencies(
    content: str,
    semantic_lines: set[int],
    *,
    max_hops: int = 2,
) -> StructuralResult:
    """Recover syntax, scope, control-flow, and symbol support."""

    if not semantic_lines:
        return StructuralResult({})
    try:
        tree = ast.parse(content)
    except SyntaxError as error:
        return StructuralResult({}, f"line {error.lineno}: {error.msg}")

    analyzer = _PythonStructure(content, tree)
    support: dict[int, set[LineReason]] = defaultdict(set)
    dependency_queue: deque[tuple[ast.AST, int]] = deque()
    queued: set[tuple[int, int]] = set()

    for line in sorted(semantic_lines):
        statement = analyzer.smallest_statement(line)
        if statement is not None:
            analyzer.add_statement_span(statement, support)
            dependency_queue.append((statement, 0))
        for ancestor in analyzer.ancestors_for_line(line):
            if isinstance(ancestor, _SCOPES):
                analyzer.add_scope_header(ancestor, support)
                dependency_queue.append((ancestor, 0))
            elif isinstance(ancestor, _COMPOUNDS):
                analyzer.add_control_structure(ancestor, support)
                dependency_queue.append((ancestor, 0))

    while dependency_queue:
        node, depth = dependency_queue.popleft()
        marker = (id(node), depth)
        if marker in queued:
            continue
        queued.add(marker)
        if depth >= max_hops:
            continue
        for name in analyzer.header_names(node):
            for definition in analyzer.resolve_bindings(name, node):
                analyzer.add_dependency(definition, support)
                dependency_queue.append((definition, depth + 1))
                for ancestor in analyzer.parents_of(definition):
                    if isinstance(ancestor, _SCOPES):
                        analyzer.add_scope_header(ancestor, support)
                    elif isinstance(ancestor, _COMPOUNDS):
                        analyzer.add_control_structure(ancestor, support)
                        dependency_queue.append((ancestor, depth + 1))

    normalized = {
        line: tuple(sorted(reasons, key=lambda item: item.value))
        for line, reasons in sorted(support.items())
        if line not in semantic_lines
    }
    return StructuralResult(normalized)


class _PythonStructure:
    def __init__(self, content: str, tree: ast.AST) -> None:
        self.lines = content.splitlines()
        self.tree = tree
        self.parents: dict[ast.AST, ast.AST] = {}
        self.bindings: dict[ast.AST, dict[str, list[ast.AST]]] = defaultdict(dict)
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent
        for parent in ast.walk(tree):
            self._record_bindings(parent)

    def _scope_of(self, node: ast.AST) -> ast.AST:
        return next(
            (
                parent
                for parent in self.parents_of(node)
                if isinstance(parent, (*_SCOPES, ast.Lambda))
            ),
            self.tree,
        )

    def _bind(self, name: str, node: ast.AST) -> None:
        self.bindings[self._scope_of(node)].setdefault(name, []).append(node)

    def resolve_bindings(self, name: str, node: ast.AST) -> tuple[ast.AST, ...]:
        """Resolve lexical names without borrowing locals from sibling functions.

        Keep all assignments in the nearest scope: choosing one requires
        control-flow analysis. Function headers evaluate in their outer scope.
        """
        scope = self._scope_of(node)
        while True:
            definitions = self.bindings.get(scope, {}).get(name)
            if definitions:
                return tuple(definitions)
            if scope is self.tree:
                return ()
            scope = self._scope_of(scope)
            # A method does not close over its class's namespace.
            while isinstance(scope, ast.ClassDef):
                scope = self._scope_of(scope)

    def _record_bindings(self, node: ast.AST) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                self._bind(alias.asname or alias.name.split(".")[0], node)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                self._bind(alias.asname or alias.name, node)
        elif isinstance(node, _SCOPES):
            self._bind(node.name, node)
        elif isinstance(node, ast.arg):
            self._bind(node.arg, node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for name in _bound_names(target):
                    self._bind(name, node)
        elif isinstance(node, ast.AnnAssign):
            for name in _bound_names(node.target):
                self._bind(name, node)

    def smallest_statement(self, line: int) -> ast.stmt | None:
        candidates = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.stmt) and _contains_line(node, line)
        ]
        return min(candidates, key=_span_size, default=None)

    def ancestors_for_line(self, line: int) -> tuple[ast.AST, ...]:
        statement = self.smallest_statement(line)
        if statement is None:
            return ()
        return self.parents_of(statement)

    def parents_of(self, node: ast.AST) -> tuple[ast.AST, ...]:
        result: list[ast.AST] = []
        current = self.parents.get(node)
        while current is not None:
            result.append(current)
            current = self.parents.get(current)
        return tuple(result)

    def add_statement_span(
        self,
        node: ast.AST,
        support: dict[int, set[LineReason]],
    ) -> None:
        if not isinstance(node, _SIMPLE_STATEMENTS):
            return
        start, end = _span(node)
        for line in range(start, end + 1):
            support[line].add(LineReason.SYNTAX)

    def add_scope_header(
        self,
        node: ast.AST,
        support: dict[int, set[LineReason]],
    ) -> None:
        assert isinstance(node, _SCOPES)
        start = min(
            (item.lineno for item in node.decorator_list),
            default=node.lineno,
        )
        header_end = max(node.lineno, node.body[0].lineno - 1)
        for line in range(start, header_end + 1):
            support[line].add(LineReason.SCOPE)

    def add_control_structure(
        self,
        node: ast.AST,
        support: dict[int, set[LineReason]],
    ) -> None:
        line = getattr(node, "lineno", None)
        if line:
            body = getattr(node, "body", [])
            header_end = max(line, body[0].lineno - 1) if body else line
            for header_line in range(line, header_end + 1):
                support[header_line].add(LineReason.CONTROL_FLOW)
        for branch_line in self._branch_headers(node):
            support[branch_line].add(LineReason.CONTROL_FLOW)

    def add_dependency(
        self,
        node: ast.AST,
        support: dict[int, set[LineReason]],
    ) -> None:
        if isinstance(node, _SCOPES):
            self.add_scope_header(node, support)
            return
        if isinstance(node, ast.arg):
            # Its enclosing function header already preserves the parameter.
            return
        start, end = _span(node)
        for line in range(start, end + 1):
            support[line].add(LineReason.DEPENDENCY)

    def header_names(self, node: ast.AST) -> set[str]:
        roots: list[ast.AST] = []
        if isinstance(node, ast.ClassDef):
            roots.extend(node.bases)
            roots.extend(node.keywords)
            roots.extend(node.decorator_list)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            roots.extend(node.decorator_list)
            roots.extend(node.args.defaults)
            roots.extend(item for item in node.args.kw_defaults if item is not None)
            roots.extend(
                item.annotation
                for item in (
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                )
                if item.annotation is not None
            )
            if node.returns is not None:
                roots.append(node.returns)
        elif isinstance(node, _COMPOUNDS):
            # Follow names in conditions/iterators, not unselected bodies.
            for field_name, value in ast.iter_fields(node):
                if field_name in {"body", "orelse", "finalbody", "handlers", "cases"}:
                    continue
                if isinstance(value, ast.AST):
                    roots.append(value)
                elif isinstance(value, list):
                    roots.extend(item for item in value if isinstance(item, ast.AST))
        else:
            roots.append(node)
        return {
            item.id
            for root in roots
            for item in ast.walk(root)
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
        }

    def _branch_headers(self, node: ast.AST) -> set[int]:
        start, end = _span(node)
        result: set[int] = set()
        pattern = re.compile(r"^\s*(?:elif\b|else\s*:|except\b|finally\s*:|case\b)")
        for line in range(start + 1, min(end, len(self.lines)) + 1):
            if pattern.match(self.lines[line - 1]):
                result.add(line)
        if isinstance(node, ast.Try):
            result.update(item.lineno for item in node.handlers)
        return result


def _bound_names(node: ast.AST) -> set[str]:
    return {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
    }


def _span(node: ast.AST) -> tuple[int, int]:
    start = int(getattr(node, "lineno", 1))
    return start, int(getattr(node, "end_lineno", start))


def _span_size(node: ast.AST) -> int:
    start, end = _span(node)
    return end - start


def _contains_line(node: ast.AST, line: int) -> bool:
    start, end = _span(node)
    return start <= line <= end
