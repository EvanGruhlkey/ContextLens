"""Repository-native discovery and conservative static context analysis."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from contextlens.trace import ContextSource, SourceKind

TOKEN_COUNT_METHOD = "estimated_utf8_bytes_div_4"
STATIC_EVIDENCE = "observed/static"

_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".release-smoke",
        ".release-smoke-current",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "artifacts",
        "build",
        "dist",
        "node_modules",
        "vendor",
    }
)
_TEXT_SUFFIXES = frozenset({".md", ".mdc", ".txt", ".json", ".jsonc", ".yaml", ".yml"})
_MCP_PATHS = frozenset(
    {
        ".mcp.json",
        "mcp.json",
        ".cursor/mcp.json",
        ".vscode/mcp.json",
        ".claude/mcp.json",
    }
)
_PATH_PREFIXES = (
    "src/",
    "lib/",
    "app/",
    "apps/",
    "package/",
    "packages/",
    "docs/",
    "test/",
    "tests/",
    "scripts/",
    "tools/",
    ".github/",
    ".cursor/",
    ".agents/",
    ".codex/",
)


def estimate_tokens(content: str) -> int:
    """Return a deterministic, explicitly approximate token count."""

    return (len(content.encode("utf-8")) + 3) // 4


@dataclass(frozen=True, slots=True)
class RepositoryContext:
    """One context-bearing repository file discovered by convention."""

    path: str
    kind: str
    scope: str
    content: str
    tokens: int
    cold_start: bool
    format: str
    token_count_method: str = TOKEN_COUNT_METHOD

    @property
    def source_id(self) -> str:
        digest = hashlib.sha256(self.path.encode("utf-8")).hexdigest()[:16]
        return f"repository:{digest}"

    def to_context_source(self) -> ContextSource:
        kind = (
            SourceKind.TOOL_SCHEMA
            if self.kind in {"mcp_configuration", "tool_schema"}
            else SourceKind.REPO_INSTRUCTION
        )
        return ContextSource(
            source_id=self.source_id,
            kind=kind,
            name=self.path,
            content=self.content,
            token_count=self.tokens,
            token_count_method=self.token_count_method,
            source_uri=self.path,
            provenance={
                "repository_path": self.path,
                "scope": self.scope,
                "discovery": "repository_convention",
                "cold_start": self.cold_start,
            },
            tags=("repository_context", self.kind),
        )

    def to_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "path": self.path,
            "kind": self.kind,
            "scope": self.scope,
            "tokens": self.tokens,
            "token_count_method": self.token_count_method,
            "cold_start": self.cold_start,
            "format": self.format,
            "content_hash": hashlib.sha256(self.content.encode("utf-8")).hexdigest(),
        }
        if include_content:
            value["content"] = self.content
        return value


@dataclass(frozen=True, slots=True)
class StaticFinding:
    """A deterministic observation that makes no causal performance claim."""

    finding_id: str
    category: str
    message: str
    paths: tuple[str, ...]
    tokens: int = 0
    detail: str = ""
    evidence: str = STATIC_EVIDENCE
    verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "category": self.category,
            "message": self.message,
            "paths": list(self.paths),
            "tokens": self.tokens,
            "detail": self.detail,
            "evidence": self.evidence,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class RepositoryScan:
    """Complete no-model-call inventory and static findings."""

    root: str
    sources: tuple[RepositoryContext, ...]
    findings: tuple[StaticFinding, ...]
    revision: str = "worktree"

    @property
    def total_tokens(self) -> int:
        return sum(source.tokens for source in self.sources)

    @property
    def cold_start_tokens(self) -> int:
        return sum(source.tokens for source in self.sources if source.cold_start)

    @property
    def duplicate_tokens(self) -> int:
        return sum(
            finding.tokens
            for finding in self.findings
            if finding.category in {"duplicate", "nested_scope_duplicate"}
        )

    @property
    def stale_reference_count(self) -> int:
        return sum(finding.category == "stale_reference" for finding in self.findings)

    def to_context_sources(self) -> tuple[ContextSource, ...]:
        return tuple(source.to_context_source() for source in self.sources)

    def to_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "report_type": "repository_context_scan",
            "evidence": STATIC_EVIDENCE,
            "verified": False,
            "root": self.root,
            "revision": self.revision,
            "summary": {
                "source_count": len(self.sources),
                "total_estimated_tokens": self.total_tokens,
                "cold_start_estimated_tokens": self.cold_start_tokens,
                "duplicate_estimated_tokens": self.duplicate_tokens,
                "stale_reference_count": self.stale_reference_count,
                "token_count_method": TOKEN_COUNT_METHOD,
            },
            "sources": [
                source.to_dict(include_content=include_content)
                for source in self.sources
            ],
            "findings": [finding.to_dict() for finding in self.findings],
            "disclaimer": (
                "Static findings identify context footprint and review candidates; "
                "they do not establish performance impact or safety of removal."
            ),
        }


@dataclass(frozen=True, slots=True)
class SourceDelta:
    """Token and presence change for one discovered path."""

    path: str
    kind: str
    base_tokens: int
    candidate_tokens: int

    @property
    def delta_tokens(self) -> int:
        return self.candidate_tokens - self.base_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "base_tokens": self.base_tokens,
            "candidate_tokens": self.candidate_tokens,
            "delta_tokens": self.delta_tokens,
        }


@dataclass(frozen=True, slots=True)
class RepositoryDiff:
    """Git-aware comparison of repository context inventory."""

    root: str
    base_ref: str
    base: RepositoryScan
    candidate: RepositoryScan
    sources: tuple[SourceDelta, ...]

    @property
    def delta_tokens(self) -> int:
        return self.candidate.total_tokens - self.base.total_tokens

    @property
    def change_fraction(self) -> float | None:
        if self.base.total_tokens == 0:
            return None
        return self.delta_tokens / self.base.total_tokens

    @property
    def duplicate_delta(self) -> int:
        return self.candidate.duplicate_tokens - self.base.duplicate_tokens

    @property
    def stale_reference_delta(self) -> int:
        return self.candidate.stale_reference_count - self.base.stale_reference_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "report_type": "repository_context_diff",
            "evidence": STATIC_EVIDENCE,
            "verified": False,
            "root": self.root,
            "base_ref": self.base_ref,
            "summary": {
                "base_estimated_tokens": self.base.total_tokens,
                "candidate_estimated_tokens": self.candidate.total_tokens,
                "delta_estimated_tokens": self.delta_tokens,
                "change_fraction": self.change_fraction,
                "duplicate_token_delta": self.duplicate_delta,
                "stale_reference_delta": self.stale_reference_delta,
                "token_count_method": TOKEN_COUNT_METHOD,
            },
            "sources": [source.to_dict() for source in self.sources],
            "base_findings": [finding.to_dict() for finding in self.base.findings],
            "candidate_findings": [
                finding.to_dict() for finding in self.candidate.findings
            ],
            "disclaimer": (
                "This is a deterministic static comparison, not evidence that "
                "the candidate helps or hurts an agent."
            ),
        }


def scan_repository(root: Path = Path(".")) -> RepositoryScan:
    """Discover and inspect common agent-context files without model calls."""

    resolved = root.resolve()
    if not resolved.is_dir():
        raise ValueError(f"repository path is not a directory: {resolved}")
    files: dict[str, str] = {}
    known_paths: set[str] = set()
    for directory, directory_names, file_names in os.walk(resolved):
        directory_path = Path(directory)
        parent_name = directory_path.name.casefold()
        directory_names[:] = [
            name
            for name in directory_names
            if name.casefold() not in _IGNORED_DIRECTORIES
            and not (name.casefold() == "fixtures" and parent_name in {"test", "tests"})
        ]
        parent = directory_path
        for file_name in file_names:
            path = parent / file_name
            relative = path.relative_to(resolved)
            normalized = relative.as_posix()
            known_paths.add(normalized)
            if classify_context_path(normalized) is None:
                continue
            try:
                files[normalized] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
    return scan_repository_files(
        resolved,
        files,
        known_paths=frozenset(known_paths),
    )


def scan_git_ref(root: Path, ref: str) -> RepositoryScan:
    """Discover context from one immutable Git tree without checking it out."""

    resolved = root.resolve()
    _git(resolved, "rev-parse", "--verify", f"{ref}^{{commit}}")
    listing = _git(resolved, "ls-tree", "-r", "--name-only", ref)
    files: dict[str, str] = {}
    known_paths: set[str] = set()
    for raw_path in listing.splitlines():
        path = raw_path.strip().replace("\\", "/")
        if not path:
            continue
        known_paths.add(path)
        if _ignored_discovery_path(path) or classify_context_path(path) is None:
            continue
        content = _git_bytes(resolved, "show", f"{ref}:{path}")
        try:
            files[path] = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return scan_repository_files(
        resolved,
        files,
        revision=ref,
        known_paths=frozenset(known_paths),
    )


def scan_repository_files(
    root: Path,
    files: Mapping[str, str],
    *,
    revision: str = "worktree",
    known_paths: frozenset[str] | None = None,
) -> RepositoryScan:
    """Analyze an explicit path/content mapping (also used for Git trees)."""

    normalized_files = {
        _normalize_path(path): content for path, content in files.items()
    }
    sources = tuple(
        _repository_context(path, normalized_files[path])
        for path in sorted(normalized_files, key=lambda value: value.casefold())
        if classify_context_path(path) is not None
    )
    repository_paths = (
        frozenset(_normalize_path(path) for path in known_paths)
        if known_paths is not None
        else frozenset(normalized_files)
    )
    findings = _static_findings(root.resolve(), sources, repository_paths)
    return RepositoryScan(
        root=str(root.resolve()),
        sources=sources,
        findings=findings,
        revision=revision,
    )


def diff_repository(
    root: Path = Path("."), *, base_ref: str | None = None
) -> RepositoryDiff:
    """Compare worktree context to a Git base ref."""

    resolved = root.resolve()
    selected_ref = base_ref or default_base_ref(resolved)
    base = scan_git_ref(resolved, selected_ref)
    candidate = scan_repository(resolved)
    by_base = {source.path: source for source in base.sources}
    by_candidate = {source.path: source for source in candidate.sources}
    sources = tuple(
        SourceDelta(
            path=path,
            kind=(by_candidate.get(path) or by_base[path]).kind,
            base_tokens=by_base[path].tokens if path in by_base else 0,
            candidate_tokens=(by_candidate[path].tokens if path in by_candidate else 0),
        )
        for path in sorted(set(by_base) | set(by_candidate), key=str.casefold)
        if (by_base[path].tokens if path in by_base else 0)
        != (by_candidate[path].tokens if path in by_candidate else 0)
        or (by_base[path].content if path in by_base else None)
        != (by_candidate[path].content if path in by_candidate else None)
    )
    return RepositoryDiff(
        root=str(resolved),
        base_ref=selected_ref,
        base=base,
        candidate=candidate,
        sources=sources,
    )


def default_base_ref(root: Path) -> str:
    """Choose a predictable comparison base, preferring the mainline merge-base."""

    for candidate in ("origin/main", "main", "origin/master", "master"):
        try:
            _git(root, "rev-parse", "--verify", f"{candidate}^{{commit}}")
        except RuntimeError:
            continue
        try:
            return _git(root, "merge-base", "HEAD", candidate).strip()
        except RuntimeError:
            return candidate
    try:
        return _git(root, "rev-parse", "HEAD^").strip()
    except RuntimeError:
        return _git(root, "rev-parse", "HEAD").strip()


def classify_context_path(path: str) -> tuple[str, str, bool] | None:
    """Return ``(kind, scope, cold_start)`` for recognized context paths."""

    normalized = _normalize_path(path)
    pure = PurePosixPath(normalized)
    name = pure.name.casefold()
    lowered = normalized.casefold()
    parent = pure.parent.as_posix()
    scope = "." if parent == "." else parent
    if name == "agents.md":
        return "agents_md", scope, scope == "."
    if name == "claude.md":
        return "claude_md", scope, scope == "."
    if lowered == ".github/copilot-instructions.md":
        return "copilot_instructions", ".", True
    if lowered.startswith(".github/instructions/") and name.endswith(
        ".instructions.md"
    ):
        return "copilot_instructions", scope, False
    if (
        lowered.startswith(".cursor/rules/")
        and pure.suffix.casefold() in _TEXT_SUFFIXES
    ):
        return "cursor_rule", scope, False
    if name == "skill.md" and any(
        part.casefold() in {"skills", ".agents", ".codex", ".claude"}
        for part in pure.parts[:-1]
    ):
        return "agent_skill", scope, False
    if lowered in _MCP_PATHS:
        return "mcp_configuration", scope, True
    if pure.suffix.casefold() == ".json" and (
        name.startswith("tool-schema")
        or name.startswith("tools-schema")
        or name in {"tool-schemas.json", "tools.json"}
    ):
        return "tool_schema", scope, True
    return None


def render_scan_terminal(report: RepositoryScan) -> str:
    """Render the fast human-facing repository inventory."""

    lines = ["ContextLens", "", "Agent context                         Tokens"]
    lines.append("-" * 49)
    for source in report.sources:
        lines.append(f"{_clip(source.path, 35):35} {source.tokens:>12,}")
    lines.append("-" * 49)
    lines.append(f"{'Total':35} {report.total_tokens:>12,}")
    lines.extend(("", "Findings (observed / static — NOT VERIFIED)"))
    if not report.findings:
        lines.append("No conservative static findings.")
    else:
        for finding in report.findings:
            suffix = f" ({finding.tokens:,} estimated tokens)" if finding.tokens else ""
            lines.append(f"- {finding.message}{suffix}")
    lines.extend(
        (
            "",
            "Static findings do not establish performance impact or safe removal.",
            "Run `contextlens verify` to measure whether a context change helps.",
        )
    )
    return "\n".join(lines) + "\n"


def render_diff_terminal(report: RepositoryDiff) -> str:
    """Render a compact base-versus-worktree context comparison."""

    lines = [
        "Agent Context Diff",
        "",
        f"Base: {report.base_ref}",
        "",
        "Source                            base    candidate       delta",
        "-" * 63,
    ]
    for source in report.sources:
        lines.append(
            f"{_clip(source.path, 30):30} "
            f"{source.base_tokens:>8,} {source.candidate_tokens:>12,} "
            f"{source.delta_tokens:>+11,}"
        )
    lines.append("-" * 63)
    lines.append(
        f"{'Initial context':30} {report.base.total_tokens:>8,} "
        f"{report.candidate.total_tokens:>12,} {report.delta_tokens:>+11,}"
    )
    change = (
        "0.0%"
        if report.base.total_tokens == report.candidate.total_tokens == 0
        else "new context"
        if report.change_fraction is None
        else f"{report.change_fraction:+.1%}"
    )
    lines.extend(
        (
            f"Change: {change}",
            "",
            "Potential waste (observed / static — NOT VERIFIED)",
            f"- duplicate context delta: {report.duplicate_delta:+,} estimated tokens",
            f"- stale reference delta: {report.stale_reference_delta:+,}",
            "",
            "Run `contextlens verify` before treating any candidate change as causal.",
        )
    )
    return "\n".join(lines) + "\n"


def render_markdown(value: RepositoryScan | RepositoryDiff) -> str:
    """Render a GitHub-summary-friendly Markdown report."""

    if isinstance(value, RepositoryDiff):
        rows = (
            "\n".join(
                f"| `{item.path}` | {item.base_tokens:,} | "
                f"{item.candidate_tokens:,} | {item.delta_tokens:+,} |"
                for item in value.sources
            )
            or "| _No discovered context changes_ | — | — | — |"
        )
        change = (
            "0.0%"
            if value.base.total_tokens == value.candidate.total_tokens == 0
            else "n/a"
            if value.change_fraction is None
            else f"{value.change_fraction:+.1%}"
        )
        return (
            "## ContextLens — Agent Context Diff\n\n"
            "**Evidence:** observed / static — **NOT VERIFIED**\n\n"
            "| Source | Base | Candidate | Delta |\n"
            "| --- | ---: | ---: | ---: |\n"
            f"{rows}\n"
            f"| **Total** | **{value.base.total_tokens:,}** | "
            f"**{value.candidate.total_tokens:,}** | **{change}** |\n\n"
            f"Duplicate context delta: **{value.duplicate_delta:+,} tokens**  \n"
            f"Stale-reference delta: **{value.stale_reference_delta:+,}**\n\n"
            "Static findings are review candidates, not causal evidence.\n"
        )
    rows = (
        "\n".join(
            f"| `{source.path}` | {source.kind} | {source.tokens:,} |"
            for source in value.sources
        )
        or "| _No recognized context_ | — | 0 |"
    )
    findings = (
        "\n".join(f"- {finding.message}" for finding in value.findings)
        or "- No conservative static findings."
    )
    return (
        "## ContextLens — Repository Context Scan\n\n"
        "**Evidence:** observed / static — **NOT VERIFIED**\n\n"
        "| Source | Type | Estimated tokens |\n"
        "| --- | --- | ---: |\n"
        f"{rows}\n"
        f"| **Total** | | **{value.total_tokens:,}** |\n\n"
        f"### Findings\n\n{findings}\n\n"
        "Static findings are review candidates, not causal evidence.\n"
    )


def _repository_context(path: str, content: str) -> RepositoryContext:
    classified = classify_context_path(path)
    assert classified is not None
    kind, scope, cold_start = classified
    return RepositoryContext(
        path=path,
        kind=kind,
        scope=scope,
        content=content,
        tokens=estimate_tokens(content),
        cold_start=cold_start,
        format=PurePosixPath(path).suffix.casefold().lstrip(".") or "text",
    )


def _static_findings(
    root: Path,
    sources: tuple[RepositoryContext, ...],
    known_paths: frozenset[str],
) -> tuple[StaticFinding, ...]:
    findings = [
        *_duplicate_findings(sources),
        *_stale_findings(root, sources, known_paths),
    ]
    findings.extend(_conflict_findings(sources))
    findings.extend(_scoping_findings(sources, known_paths))
    tool_tokens = sum(
        source.tokens
        for source in sources
        if source.kind in {"mcp_configuration", "tool_schema"}
    )
    if tool_tokens:
        paths = tuple(
            source.path
            for source in sources
            if source.kind in {"mcp_configuration", "tool_schema"}
        )
        findings.append(
            StaticFinding(
                finding_id="tool-schema-footprint",
                category="tool_schema_footprint",
                message=(
                    "tool and MCP configuration contributes "
                    f"{tool_tokens:,} estimated tokens"
                ),
                paths=paths,
                tokens=tool_tokens,
                detail=(
                    "Footprint only; runtime tool-search and provider behavior "
                    "may differ."
                ),
            )
        )
    return tuple(
        sorted(findings, key=lambda item: (item.category, item.paths, item.message))
    )


def _duplicate_findings(
    sources: tuple[RepositoryContext, ...],
) -> tuple[StaticFinding, ...]:
    fragments: list[tuple[RepositoryContext, str, frozenset[str]]] = []
    for source in sources:
        for fragment in _instruction_fragments(source.content):
            words = _words(fragment)
            if len(words) >= 5:
                fragments.append((source, fragment, words))
    findings: list[StaticFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for index, (left_source, left_text, left_words) in enumerate(fragments):
        for right_source, right_text, right_words in fragments[index + 1 :]:
            if left_source.path == right_source.path:
                continue
            pair = tuple(sorted((left_source.path, right_source.path)))
            fingerprint = _normalize_instruction(left_text)
            key = (pair[0], pair[1], fingerprint)
            if key in seen:
                continue
            exact = fingerprint == _normalize_instruction(right_text)
            similarity = len(left_words & right_words) / len(left_words | right_words)
            if not exact and similarity < 0.86:
                continue
            seen.add(key)
            nested = _nested_scopes(left_source.scope, right_source.scope)
            category = "nested_scope_duplicate" if nested else "duplicate"
            tokens = min(estimate_tokens(left_text), estimate_tokens(right_text))
            findings.append(
                StaticFinding(
                    finding_id=_finding_id(category, pair, fingerprint),
                    category=category,
                    message=(
                        f"{tokens:,} estimated tokens repeated across "
                        f"{pair[0]} and {pair[1]}"
                    ),
                    paths=pair,
                    tokens=tokens,
                    detail=(
                        "Exact repeated instruction."
                        if exact
                        else (
                            "Near-duplicate instruction "
                            f"({similarity:.0%} word overlap)."
                        )
                    ),
                )
            )
    return tuple(findings)


def _stale_findings(
    root: Path,
    sources: tuple[RepositoryContext, ...],
    known_paths: frozenset[str],
) -> tuple[StaticFinding, ...]:
    findings: list[StaticFinding] = []
    for source in sources:
        source_parent = PurePosixPath(source.path).parent
        for reference in sorted(_path_references(source.content)):
            if _reference_exists(root, source_parent, reference, known_paths):
                continue
            findings.append(
                StaticFinding(
                    finding_id=_finding_id("stale", (source.path,), reference),
                    category="stale_reference",
                    message=f"{source.path} references missing path `{reference}`",
                    paths=(source.path,),
                    detail=(
                        "The path was explicit and was not found in the worktree "
                        "or source-relative scope."
                    ),
                )
            )
    return tuple(findings)


def _conflict_findings(
    sources: tuple[RepositoryContext, ...],
) -> tuple[StaticFinding, ...]:
    statements: list[tuple[RepositoryContext, str, int, frozenset[str]]] = []
    for source in sources:
        for fragment in _instruction_fragments(source.content):
            polarity = _polarity(fragment)
            if polarity:
                statements.append((source, fragment, polarity, _action_words(fragment)))
    findings: list[StaticFinding] = []
    for index, (left_source, left, left_polarity, left_words) in enumerate(statements):
        for right_source, right, right_polarity, right_words in statements[index + 1 :]:
            if left_source.path == right_source.path or left_polarity == right_polarity:
                continue
            if not left_words or not right_words:
                continue
            overlap = len(left_words & right_words) / len(left_words | right_words)
            if overlap < 0.7:
                continue
            paths = tuple(sorted((left_source.path, right_source.path)))
            findings.append(
                StaticFinding(
                    finding_id=_finding_id(
                        "conflict", paths, " ".join(sorted(left_words))
                    ),
                    category="potential_conflict",
                    message=(
                        "potentially conflicting modal instructions in "
                        f"{paths[0]} and {paths[1]}"
                    ),
                    paths=paths,
                    detail=f"`{_clip(left, 90)}` versus `{_clip(right, 90)}`",
                )
            )
    return tuple(findings)


def _scoping_findings(
    sources: tuple[RepositoryContext, ...],
    known_paths: frozenset[str],
) -> tuple[StaticFinding, ...]:
    top_levels = {PurePosixPath(path).parts[0] for path in known_paths if "/" in path}
    findings: list[StaticFinding] = []
    for source in sources:
        if source.scope != "." or source.kind not in {
            "agents_md",
            "claude_md",
            "copilot_instructions",
        }:
            continue
        for fragment in _instruction_fragments(source.content):
            referenced = {
                top
                for top in top_levels
                if re.search(rf"(?<![\w.-]){re.escape(top)}/", fragment)
            }
            if len(referenced) != 1:
                continue
            target = next(iter(referenced))
            tokens = estimate_tokens(fragment)
            findings.append(
                StaticFinding(
                    finding_id=_finding_id("scope", (source.path,), fragment),
                    category="potential_scope",
                    message=(
                        f"{tokens:,} estimated tokens in {source.path} "
                        f"mention only `{target}/`"
                    ),
                    paths=(source.path,),
                    tokens=tokens,
                    detail=(
                        f"Candidate for review as {target}-scoped guidance; "
                        "not verified safe to move."
                    ),
                )
            )
    return tuple(findings)


def _instruction_fragments(content: str) -> tuple[str, ...]:
    fragments: list[str] = []
    paragraph: list[str] = []
    in_fence = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            if paragraph:
                fragments.append(" ".join(paragraph))
                paragraph = []
            continue
        if in_fence or line.startswith("#"):
            continue
        if not line:
            if paragraph:
                fragments.append(" ".join(paragraph))
                paragraph = []
            continue
        if re.match(r"^(?:[-*+] |\d+[.)] )", line):
            if paragraph:
                fragments.append(" ".join(paragraph))
                paragraph = []
            fragments.append(re.sub(r"^(?:[-*+] |\d+[.)] )", "", line))
        else:
            paragraph.append(line)
    if paragraph:
        fragments.append(" ".join(paragraph))
    return tuple(fragment for fragment in fragments if fragment.strip())


def _path_references(content: str) -> frozenset[str]:
    candidates: set[str] = set()
    for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", content):
        candidates.add(match.group(1).split("#", 1)[0].strip())
    for match in re.finditer(r"`([^`\r\n]+)`", content):
        value = match.group(1).strip()
        if _looks_like_path(value):
            candidates.add(value)
    for prefix in _PATH_PREFIXES:
        pattern = rf"(?<![\w.-])({re.escape(prefix)}[\w.@+\-/\\*?]+)"
        candidates.update(match.group(1) for match in re.finditer(pattern, content))
    return frozenset(_clean_reference(value) for value in candidates if value)


def _looks_like_path(value: str) -> bool:
    if " " in value or value.startswith(("http://", "https://", "#", "--")):
        return False
    if value in {".", "..", "/"}:
        return False
    return "/" in value or "\\" in value


def _clean_reference(value: str) -> str:
    value = value.strip().strip("'\"").replace("\\", "/")
    value = re.sub(r":\d+(?::\d+)?$", "", value)
    return value.removeprefix("./").rstrip(".,;:")


def _reference_exists(
    root: Path,
    source_parent: PurePosixPath,
    reference: str,
    known_paths: frozenset[str],
) -> bool:
    del root
    if (
        not reference
        or reference.startswith("#")
        or re.match(r"^[a-z][a-z0-9+.-]*:", reference, re.IGNORECASE)
        or re.match(r"^[a-z]:/", reference, re.IGNORECASE)
        or reference.startswith("/")
    ):
        return True
    candidates = {
        _normalize_path(reference),
        _normalize_path((source_parent / reference).as_posix()),
    }
    for candidate in candidates:
        if any(character in candidate for character in "*?["):
            if any(fnmatch.fnmatch(path, candidate) for path in known_paths):
                return True
            continue
        if candidate in known_paths or any(
            path.startswith(f"{candidate}/") for path in known_paths
        ):
            return True
    return False


def _polarity(value: str) -> int:
    normalized = value.casefold()
    if re.search(r"\b(?:must not|do not|don't|never|cannot|can't)\b", normalized):
        return -1
    if re.search(r"\b(?:must|always|required to|should)\b", normalized):
        return 1
    return 0


def _action_words(value: str) -> frozenset[str]:
    stop = {
        "always",
        "cannot",
        "do",
        "don't",
        "must",
        "never",
        "not",
        "required",
        "should",
        "the",
        "to",
        "you",
    }
    return frozenset(word for word in _words(value) if word not in stop)


def _words(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9_.-]+", value.casefold()))


def _normalize_instruction(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9_.-]+", value.casefold()))


def _nested_scopes(left: str, right: str) -> bool:
    if left == right:
        return False
    return (
        left == "."
        or right == "."
        or left.startswith(f"{right}/")
        or right.startswith(f"{left}/")
    )


def _finding_id(category: str, paths: Iterable[str], value: str) -> str:
    encoded = "\0".join((category, *paths, value)).encode("utf-8")
    return f"{category}:{hashlib.sha256(encoded).hexdigest()[:16]}"


def _normalize_path(path: str) -> str:
    normalized = PurePosixPath(path.replace("\\", "/")).as_posix()
    return normalized.removeprefix("./")


def _ignored_discovery_path(path: str) -> bool:
    parts = tuple(part.casefold() for part in PurePosixPath(path).parts)
    return any(
        left in {"test", "tests"} and right == "fixtures"
        for left, right in zip(parts, parts[1:], strict=False)
    )


def _clip(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(arguments)} failed: {completed.stderr.strip()}"
        )
    return completed.stdout


def _git_bytes(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"git {' '.join(arguments)} failed: {stderr.strip()}")
    return completed.stdout


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write stable machine-readable output for CI integrations."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
