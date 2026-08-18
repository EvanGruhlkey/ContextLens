"""Verified minimization built on static candidates and regression replays."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contextlens.regression import (
    RegressionVerdict,
    VerificationReport,
    verify_context_candidate,
)
from contextlens.repository import (
    RepositoryContext,
    RepositoryScan,
    estimate_tokens,
    scan_repository,
)


@dataclass(frozen=True, slots=True)
class MinimizationEdit:
    """One static candidate edit; never a safety claim by itself."""

    path: str
    operation: str
    description: str
    removed_text: str
    estimated_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "operation": self.operation,
            "description": self.description,
            "estimated_tokens": self.estimated_tokens,
            "evidence": "candidate/static",
            "verified": False,
        }


@dataclass(frozen=True, slots=True)
class MinimizationCandidate:
    """A non-destructive in-memory candidate generated from static evidence."""

    original: RepositoryScan
    sources: tuple[RepositoryContext, ...]
    edits: tuple[MinimizationEdit, ...]

    @property
    def original_tokens(self) -> int:
        return self.original.total_tokens

    @property
    def candidate_tokens(self) -> int:
        return sum(source.tokens for source in self.sources)

    @property
    def saved_tokens(self) -> int:
        return self.original_tokens - self.candidate_tokens

    def context_sources(self) -> tuple[Any, ...]:
        return tuple(source.to_context_source() for source in self.sources)


@dataclass(frozen=True, slots=True)
class MinimizationReport:
    """Static candidate plus optional target-model verification evidence."""

    candidate: MinimizationCandidate
    verification: VerificationReport | None
    recommended: bool
    patch: str | None
    status: str
    rationale: str

    @property
    def exit_code(self) -> int:
        if self.verification is None:
            return 0
        return 0 if self.recommended else 4

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "report_type": "verified_context_minimization",
            "status": self.status,
            "recommended": self.recommended,
            "rationale": self.rationale,
            "summary": {
                "original_estimated_tokens": self.candidate.original_tokens,
                "candidate_estimated_tokens": self.candidate.candidate_tokens,
                "saved_estimated_tokens": self.candidate.saved_tokens,
                "reduction_fraction": (
                    self.candidate.saved_tokens / self.candidate.original_tokens
                    if self.candidate.original_tokens
                    else 0.0
                ),
            },
            "candidate_edits": [edit.to_dict() for edit in self.candidate.edits],
            "verification": (
                self.verification.to_dict() if self.verification is not None else None
            ),
            "patch": self.patch,
        }


def minimize_repository(
    root: Path,
    *,
    config_path: Path | None = None,
    selected_paths: tuple[str, ...] = (),
    max_candidates: int = 8,
) -> MinimizationReport:
    """Generate exact-duplicate removals and verify the combined patch."""

    scan = scan_repository(root)
    candidate = build_minimization_candidate(
        scan,
        selected_paths=selected_paths,
        max_candidates=max_candidates,
    )
    if not candidate.edits:
        return MinimizationReport(
            candidate=candidate,
            verification=None,
            recommended=False,
            patch=None,
            status="no_candidate",
            rationale="No conservative exact-duplicate edit candidates were found.",
        )
    patch = render_candidate_patch(candidate)
    if config_path is None:
        return MinimizationReport(
            candidate=candidate,
            verification=None,
            recommended=False,
            patch=patch,
            status="candidate",
            rationale=(
                "Static candidates were generated but are NOT VERIFIED. "
                "Provide --config to test them before recommendation."
            ),
        )
    verification = verify_context_candidate(
        config_path,
        base_context=scan.to_context_sources(),
        candidate_context=tuple(candidate.context_sources()),
        root=root,
        base_label="current-context",
    )
    recommended = minimization_is_safe(
        verification.verdict,
        saved_tokens=candidate.saved_tokens,
    )
    return MinimizationReport(
        candidate=candidate,
        verification=verification,
        recommended=recommended,
        patch=patch if recommended else None,
        status="verified_improvement" if recommended else "rejected",
        rationale=(
            "Combined candidate preserved measured quality and passed the "
            "configured economics gate."
            if recommended
            else (
                "Candidate was not recommended because combined verification "
                "did not pass."
            )
        ),
    )


def build_minimization_candidate(
    scan: RepositoryScan,
    *,
    selected_paths: tuple[str, ...] = (),
    max_candidates: int = 8,
) -> MinimizationCandidate:
    """Turn exact repeated instructions into a bounded review candidate."""

    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")
    selected = set(selected_paths)
    contents = {source.path: source.content for source in scan.sources}
    edits: list[MinimizationEdit] = []
    occurrences: dict[str, list[tuple[RepositoryContext, str]]] = {}
    for source in scan.sources:
        for fragment in _fragments(source.content):
            normalized = _normalize(fragment)
            if len(normalized.split()) < 5:
                continue
            occurrences.setdefault(normalized, []).append((source, fragment))
    for normalized in sorted(occurrences):
        items = occurrences[normalized]
        paths = {source.path for source, _ in items}
        if len(paths) < 2:
            continue
        keep = min(items, key=lambda item: (_scope_depth(item[0].scope), item[0].path))
        for source, fragment in sorted(items, key=lambda item: item[0].path):
            if source.path == keep[0].path:
                continue
            if selected and source.path not in selected:
                continue
            current = contents[source.path]
            updated = _remove_fragment(current, fragment)
            if updated == current:
                continue
            contents[source.path] = updated
            edits.append(
                MinimizationEdit(
                    path=source.path,
                    operation="remove_exact_duplicate",
                    description=f"remove instruction duplicated in {keep[0].path}",
                    removed_text=fragment,
                    estimated_tokens=estimate_tokens(fragment),
                )
            )
            if len(edits) >= max_candidates:
                break
        if len(edits) >= max_candidates:
            break
    sources = tuple(
        RepositoryContext(
            path=source.path,
            kind=source.kind,
            scope=source.scope,
            content=contents[source.path],
            tokens=estimate_tokens(contents[source.path]),
            cold_start=source.cold_start,
            format=source.format,
        )
        for source in scan.sources
    )
    return MinimizationCandidate(scan, sources, tuple(edits))


def minimization_is_safe(
    verdict: RegressionVerdict,
    *,
    saved_tokens: int,
) -> bool:
    """Only a measured PASS with a smaller footprint can be recommended."""

    return verdict is RegressionVerdict.PASS and saved_tokens > 0


def render_candidate_patch(candidate: MinimizationCandidate) -> str:
    """Create a patch artifact without modifying repository files."""

    before = {source.path: source.content for source in candidate.original.sources}
    after = {source.path: source.content for source in candidate.sources}
    lines: list[str] = []
    for path in sorted(before):
        if before[path] == after[path]:
            continue
        lines.extend(
            difflib.unified_diff(
                before[path].splitlines(keepends=True),
                after[path].splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
    return "".join(lines)


def render_minimization_terminal(report: MinimizationReport) -> str:
    """Render candidate and verification labels prominently."""

    candidate = report.candidate
    reduction = (
        candidate.saved_tokens / candidate.original_tokens
        if candidate.original_tokens
        else 0.0
    )
    lines = [
        "ContextLens Minimize",
        "",
        f"Original:  {candidate.original_tokens:,} estimated tokens",
        (
            f"Candidate: {candidate.candidate_tokens:,} estimated tokens "
            f"({reduction:.1%} reduction)"
        ),
        "",
        "Candidate changes",
    ]
    if candidate.edits:
        lines.extend(
            (
                f"- {edit.path}: {edit.description} "
                f"(-{edit.estimated_tokens:,} estimated tokens)"
            )
            for edit in candidate.edits
        )
    else:
        lines.append("- none")
    lines.extend(("", f"STATUS: {report.status.upper()}", report.rationale))
    if report.verification is not None:
        lines.append(f"Verification verdict: {report.verification.verdict.value}")
    if report.patch is not None and report.recommended:
        lines.append(
            "A suggested patch can be written; source files were not modified."
        )
    return "\n".join(lines) + "\n"


def _fragments(content: str) -> tuple[str, ...]:
    fragments: list[str] = []
    paragraph: list[str] = []
    in_fence = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            if paragraph:
                fragments.append("\n".join(paragraph))
                paragraph = []
            continue
        if in_fence or line.startswith("#"):
            continue
        if not line:
            if paragraph:
                fragments.append("\n".join(paragraph))
                paragraph = []
            continue
        if re.match(r"^(?:[-*+] |\d+[.)] )", line):
            if paragraph:
                fragments.append("\n".join(paragraph))
                paragraph = []
            fragments.append(raw_line)
        else:
            paragraph.append(raw_line)
    if paragraph:
        fragments.append("\n".join(paragraph))
    return tuple(fragments)


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9_.-]+", value.casefold()))


def _remove_fragment(content: str, fragment: str) -> str:
    updated = content.replace(fragment, "", 1)
    updated = re.sub(r"\n{3,}", "\n\n", updated)
    return updated.lstrip("\n")


def _scope_depth(scope: str) -> int:
    return 0 if scope == "." else len(Path(scope).parts)
