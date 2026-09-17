"""Content-addressed repository indexing and conservative import resolution."""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import re
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

INDEX_VERSION = "evidence-v2"
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
TEXT_SUFFIXES = {".md", ".txt", ".json", ".toml", ".yaml", ".yml"}


def terms(text: str) -> list[str]:
    """Split identifiers, including snake_case and camelCase, into search terms."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return re.findall(r"[a-z][a-z0-9]*", text.lower())


@dataclass(frozen=True)
class Unit:
    path: str
    start_line: int
    end_line: int
    text: str
    bindings: list[str]
    references: list[str]
    imports: list[dict[str, str]]
    language: str
    fallback: bool = False

    @property
    def key(self) -> str:
        return f"{self.path}:{self.start_line}:{self.end_line}"


@dataclass
class RepositoryIndex:
    root: Path
    units: list[Unit]
    sources: dict[str, str]
    hashes: dict[str, str]
    skipped: list[dict[str, str]]
    cache_hits: int
    commit: str | None

    @property
    def version(self) -> str:
        return hashlib.sha256(
            json.dumps(self.hashes, sort_keys=True).encode()
        ).hexdigest()

    def dependencies(self, unit: Unit) -> tuple[list[Unit], list[dict[str, str]]]:
        """Resolve same-file symbols and local imports; report unresolved imports."""
        found: dict[str, Unit] = {}
        unresolved = []
        refs = set(unit.references)
        for other in self.units:
            if (
                other.path == unit.path
                and other.key != unit.key
                and refs & set(other.bindings)
            ):
                found[other.key] = other
        imports = [
            entry
            for other in self.units
            if other.path == unit.path
            for entry in other.imports
        ]
        for entry in imports:
            alias = entry["alias"]
            if alias not in refs and not any(
                ref.startswith(alias + ".") for ref in refs
            ):
                continue
            paths = self._resolve_module(unit.path, entry["module"], unit.language)
            if not paths:
                unresolved.append(
                    {
                        "path": unit.path,
                        "symbol": alias,
                        "module": entry["module"],
                        "reason": "external_or_unresolved_import",
                    }
                )
                continue
            if len(paths) > 1:
                unresolved.append(
                    {
                        "path": unit.path,
                        "symbol": alias,
                        "module": entry["module"],
                        "reason": "ambiguous_module",
                    }
                )
                continue
            symbols = (
                {entry["symbol"]}
                if entry["symbol"]
                else {
                    ref[len(alias) + 1 :].split(".")[0]
                    for ref in refs
                    if ref.startswith(alias + ".")
                }
            )
            candidates = [
                other
                for other in self.units
                if other.path == paths[0]
                and (not symbols or symbols & set(other.bindings))
            ]
            if not candidates:
                unresolved.append(
                    {
                        "path": unit.path,
                        "symbol": alias,
                        "module": entry["module"],
                        "reason": "unresolved_export",
                    }
                )
            for other in candidates:
                found[other.key] = other
        return list(found.values()), unresolved

    def _resolve_module(self, source: str, module: str, language: str) -> list[str]:
        parent = PurePosixPath(source).parent
        if language == "python":
            level = len(module) - len(module.lstrip("."))
            stem = module[level:].replace(".", "/")
            if level:
                for _ in range(level - 1):
                    parent = parent.parent
                base = str(parent / stem)
                candidates = [base + ".py", base + "/__init__.py"]
                return [path for path in candidates if path in self.sources]
            endings = (stem + ".py", stem + "/__init__.py")
            return [
                path
                for path in self.sources
                if any(path == end or path.endswith("/" + end) for end in endings)
            ]
        if not module.startswith("."):
            return []
        absolute = (self.root / str(parent) / module).resolve()
        if not absolute.is_relative_to(self.root):
            return []
        base = absolute.relative_to(self.root).as_posix()
        candidates = [
            base,
            *[base + suffix for suffix in SOURCE_SUFFIXES],
            *[base + "/index" + suffix for suffix in SOURCE_SUFFIXES],
        ]
        return [path for path in candidates if path in self.sources]


def build_index(root: Path, cache: Path | None = None) -> RepositoryIndex:
    root = root.resolve()
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        raise ValueError("evidence retrieval requires a Git repository") from error
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, check=False
    )
    connection = None
    parser_versions = []
    for package in ("tree-sitter", "tree-sitter-javascript", "tree-sitter-typescript"):
        try:
            parser_versions.append(importlib.metadata.version(package))
        except importlib.metadata.PackageNotFoundError:
            parser_versions.append("unavailable")
    parser_signature = ":".join(parser_versions)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(cache)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS units "
            "(key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
    units: list[Unit] = []
    sources: dict[str, str] = {}
    hashes: dict[str, str] = {}
    skipped: list[dict[str, str]] = []
    hits = 0
    try:
        names = sorted(set(listed.stdout.decode("utf-8").split("\0")) - {""})
        for name in names:
            path = root / name
            if ".contextlens" in PurePosixPath(name).parts:
                continue
            if path.suffix not in SOURCE_SUFFIXES | TEXT_SUFFIXES:
                continue
            if not path.resolve().is_relative_to(root):
                skipped.append({"path": name, "reason": "outside_repository"})
                continue
            try:
                if path.stat().st_size > 1024 * 1024:
                    skipped.append({"path": name, "reason": "file_exceeds_1_mib"})
                    continue
                content = path.read_bytes().decode("utf-8")
                digest = hashlib.sha256(content.encode()).hexdigest()
                key = (
                    f"{INDEX_VERSION}:{parser_signature}:{path.suffix}:{digest}:{name}"
                )
                row = (
                    connection.execute(
                        "SELECT payload FROM units WHERE key=?", (key,)
                    ).fetchone()
                    if connection
                    else None
                )
                if row:
                    parsed = [Unit(**item) for item in json.loads(row[0])]
                    hits += 1
                else:
                    parsed = parse_units(name, content)
                    if connection:
                        connection.execute(
                            "INSERT OR REPLACE INTO units VALUES (?,?)",
                            (key, json.dumps([asdict(u) for u in parsed])),
                        )
                units.extend(parsed)
                sources[name] = content
                hashes[name] = digest
            except SyntaxError:
                # Report invalid Python instead of silently indexing a partial AST.
                skipped.append({"path": name, "reason": "unreadable_or_invalid_python"})
            except (OSError, UnicodeError):
                skipped.append({"path": name, "reason": "unreadable_source"})
        if connection:
            connection.commit()
    finally:
        if connection:
            connection.close()
    return RepositoryIndex(
        root,
        units,
        sources,
        hashes,
        skipped,
        hits,
        commit.stdout.decode().strip() if commit.returncode == 0 else None,
    )


def parse_units(path: str, content: str) -> list[Unit]:
    suffix = PurePosixPath(path).suffix
    lines = content.splitlines(keepends=True)
    if suffix == ".py":
        tree = ast.parse(content)
        result = []
        for node in tree.body:
            decorators = getattr(node, "decorator_list", [])
            start = min([node.lineno, *[item.lineno for item in decorators]])
            end = node.end_lineno or node.lineno
            bindings = {
                item.id
                for item in ast.walk(node)
                if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
            }
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bindings = {node.name}
            imports = []
            for item in ast.walk(node):
                if isinstance(item, ast.Import):
                    for alias in item.names:
                        bound = alias.asname or alias.name.split(".")[0]
                        imports.append(
                            {"alias": bound, "module": alias.name, "symbol": ""}
                        )
                elif isinstance(item, ast.ImportFrom):
                    for alias in item.names:
                        bound = alias.asname or alias.name
                        imports.append(
                            {
                                "alias": bound,
                                "module": "." * item.level + (item.module or ""),
                                "symbol": alias.name,
                            }
                        )
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bindings = {item["alias"] for item in imports}
            refs = {
                item.id
                for item in ast.walk(node)
                if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
            }
            for item in ast.walk(node):
                if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name):
                    refs.add(f"{item.value.id}.{item.attr}")
            result.append(
                Unit(
                    path,
                    start,
                    end,
                    "".join(lines[start - 1 : end]),
                    sorted(bindings),
                    sorted(refs),
                    imports,
                    "python",
                )
            )
        return result
    if suffix in SOURCE_SUFFIXES:
        try:
            return _parse_javascript(path, content)
        except ImportError:
            pass
    return (
        [Unit(path, 1, len(lines), content, [], [], [], "text", True)] if lines else []
    )


def _parse_javascript(path: str, content: str) -> list[Unit]:
    from tree_sitter import Language, Parser

    suffix = PurePosixPath(path).suffix
    if suffix in {".ts", ".tsx"}:
        import tree_sitter_typescript as grammar

        capsule = (
            grammar.language_tsx()
            if suffix == ".tsx"
            else grammar.language_typescript()
        )
    else:
        import tree_sitter_javascript as js_grammar

        capsule = js_grammar.language()
    data = content.encode()
    tree = Parser(Language(capsule)).parse(data)
    lines = content.splitlines(keepends=True)
    if tree.root_node.has_error:
        return [Unit(path, 1, len(lines), content, [], [], [], "javascript", True)]
    result = []
    for node in tree.root_node.named_children:
        if node.type == "comment":
            continue
        start, end = node.start_point.row + 1, node.end_point.row + 1
        inner = (
            node.child_by_field_name("declaration")
            if node.type == "export_statement"
            else node
        )
        inner = inner or node
        name = inner.child_by_field_name("name")
        bindings = [data[name.start_byte : name.end_byte].decode()] if name else []
        descendants: list[Any] = [inner]
        refs: set[str] = set()
        imports: list[dict[str, str]] = []
        while descendants:
            current = descendants.pop()
            descendants.extend(current.named_children)
            if current.type in {"identifier", "type_identifier"}:
                refs.add(data[current.start_byte : current.end_byte].decode())
            if current.type == "variable_declarator" and inner.type in {
                "lexical_declaration",
                "variable_declaration",
            }:
                binding = current.child_by_field_name("name")
                if binding:
                    bindings.append(
                        data[binding.start_byte : binding.end_byte].decode()
                    )
            if current.type == "member_expression":
                obj = current.child_by_field_name("object")
                prop = current.child_by_field_name("property")
                if obj and prop:
                    refs.add(data[obj.start_byte : prop.end_byte].decode())
        if inner.type == "import_statement":
            source = inner.child_by_field_name("source")
            module = (
                data[source.start_byte : source.end_byte].decode().strip("\"'")
                if source
                else ""
            )
            descendants = list(inner.named_children)
            while descendants:
                current = descendants.pop()
                descendants.extend(current.named_children)
                if current.type == "import_specifier":
                    original = current.child_by_field_name("name")
                    alias = current.child_by_field_name("alias") or original
                    if original and alias:
                        symbol = data[original.start_byte : original.end_byte].decode()
                        bound = data[alias.start_byte : alias.end_byte].decode()
                        imports.append(
                            {"alias": bound, "module": module, "symbol": symbol}
                        )
                        bindings.append(bound)
                elif current.type == "namespace_import":
                    bound = data[
                        current.named_children[-1].start_byte : current.named_children[
                            -1
                        ].end_byte
                    ].decode()
                    imports.append({"alias": bound, "module": module, "symbol": ""})
                    bindings.append(bound)
                elif (
                    current.type == "identifier"
                    and current.parent.type == "import_clause"
                ):
                    bound = data[current.start_byte : current.end_byte].decode()
                    imports.append(
                        {"alias": bound, "module": module, "symbol": "default"}
                    )
                    bindings.append(bound)
        if node.type == "export_statement" and data[
            node.start_byte : node.end_byte
        ].startswith(b"export default"):
            bindings.append("default")
        result.append(
            Unit(
                path,
                start,
                end,
                "".join(lines[start - 1 : end]),
                sorted(set(bindings)),
                sorted(refs),
                imports,
                "typescript" if suffix in {".ts", ".tsx"} else "javascript",
            )
        )
    return result
