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
    scan_repository_files,
)


@dataclass(frozen=True, slots=True)
class MinimizationEdit:
    """One static candidate edit; never a safety claim by itself."""

    path: str
    operation: str
    description: str
    removed_text: str
    estimated_tokens: int
    replacement_path: str | None = None
    replacement_text: str | None = None
    confidence: float = 1.0
    signal: str = "static"

    @property
    def priority(self) -> float:
        return self.estimated_tokens * self.confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "operation": self.operation,
            "description": self.description,
            "estimated_tokens": self.estimated_tokens,
            "replacement_path": self.replacement_path,
            "priority": self.priority,
            "signal": self.signal,
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

    def repository_scan(self) -> RepositoryScan:
        """Materialize the in-memory candidate for task-effective verification."""

        contents = {source.path: source.content for source in self.sources}
        return scan_repository_files(
            Path(self.original.root),
            contents,
            revision="minimization-candidate",
            known_paths=self.original.known_paths | frozenset(contents),
        )


@dataclass(frozen=True, slots=True)
class CandidateExperiment:
    """One explainable candidate and its optional isolated verification."""

    candidate: MinimizationCandidate
    verification: VerificationReport | None
    accepted: bool
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "edits": [edit.to_dict() for edit in self.candidate.edits],
            "candidate_estimated_tokens": self.candidate.candidate_tokens,
            "saved_estimated_tokens": self.candidate.saved_tokens,
            "accepted": self.accepted,
            "rationale": self.rationale,
            "verification": (
                self.verification.to_dict() if self.verification is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class MinimizationReport:
    """Static candidate plus optional target-model verification evidence."""

    candidate: MinimizationCandidate
    verification: VerificationReport | None
    recommended: bool
    patch: str | None
    status: str
    rationale: str
    experiments: tuple[CandidateExperiment, ...] = ()

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
            "candidate_experiments": [
                experiment.to_dict() for experiment in self.experiments
            ],
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
    """Prioritize candidates, test them independently, then verify the combination."""

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
    experiments: list[CandidateExperiment] = []
    accepted_edits: list[MinimizationEdit] = []
    for edit in candidate.edits:
        isolated = _candidate_from_edits(scan, (edit,))
        if isolated.saved_tokens <= 0:
            experiments.append(
                CandidateExperiment(
                    candidate=isolated,
                    verification=None,
                    accepted=False,
                    rationale=(
                        "Candidate changes scope rather than repository footprint; "
                        "it remains a review proposal until target-effective replay "
                        "is configured."
                    ),
                )
            )
            continue
        isolated_verification = verify_context_candidate(
            config_path,
            base_context=scan.to_context_sources(),
            candidate_context=tuple(isolated.context_sources()),
            base_scan=scan,
            candidate_scan=isolated.repository_scan(),
            root=root,
            base_label="current-context",
        )
        accepted = minimization_is_safe(
            isolated_verification.verdict,
            saved_tokens=isolated.saved_tokens,
        )
        experiments.append(
            CandidateExperiment(
                candidate=isolated,
                verification=isolated_verification,
                accepted=accepted,
                rationale=(
                    "Candidate passed isolated quality and economics gates."
                    if accepted
                    else (
                        "Candidate failed or was inconclusive in isolated verification."
                    )
                ),
            )
        )
        if accepted:
            accepted_edits.append(edit)
    if not accepted_edits:
        return MinimizationReport(
            candidate=candidate,
            verification=None,
            recommended=False,
            patch=None,
            status="rejected",
            rationale="No candidate passed isolated fail-closed verification.",
            experiments=tuple(experiments),
        )
    combined = _candidate_from_edits(scan, tuple(accepted_edits))
    verification = verify_context_candidate(
        config_path,
        base_context=scan.to_context_sources(),
        candidate_context=tuple(combined.context_sources()),
        base_scan=scan,
        candidate_scan=combined.repository_scan(),
        root=root,
        base_label="current-context-final-combination",
    )
    recommended = minimization_is_safe(
        verification.verdict,
        saved_tokens=combined.saved_tokens,
    )
    return MinimizationReport(
        candidate=combined,
        verification=verification,
        recommended=recommended,
        patch=render_candidate_patch(combined) if recommended else None,
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
        experiments=tuple(experiments),
    )


def build_minimization_candidate(
    scan: RepositoryScan,
    *,
    selected_paths: tuple[str, ...] = (),
    max_candidates: int = 8,
) -> MinimizationCandidate:
    """Turn prioritized static signals into one bounded review candidate."""

    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")
    edits = generate_minimization_edits(
        scan,
        selected_paths=selected_paths,
        max_candidates=max_candidates,
    )
    return _candidate_from_edits(scan, edits)


def generate_minimization_edits(
    scan: RepositoryScan,
    *,
    selected_paths: tuple[str, ...] = (),
    max_candidates: int = 8,
) -> tuple[MinimizationEdit, ...]:
    """Generate explainable remove, deduplicate, and scope experiments."""

    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")
    selected = set(selected_paths)
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
            edits.append(
                MinimizationEdit(
                    path=source.path,
                    operation="deduplicate",
                    description=f"remove instruction duplicated in {keep[0].path}",
                    removed_text=fragment,
                    estimated_tokens=estimate_tokens(fragment),
                    confidence=0.98,
                    signal="exact_duplicate",
                )
            )
    by_path = {source.path: source for source in scan.sources}
    for finding in scan.findings:
        if finding.category == "stale_reference" and finding.paths:
            stale_source = by_path.get(finding.paths[0])
            reference_match = re.search(r"`([^`]+)`", finding.message)
            if stale_source is None or reference_match is None:
                continue
            reference = reference_match.group(1)
            stale_fragment = next(
                (
                    item
                    for item in _fragments(stale_source.content)
                    if reference in item
                ),
                None,
            )
            if stale_fragment is not None and (
                not selected or stale_source.path in selected
            ):
                edits.append(
                    MinimizationEdit(
                        path=stale_source.path,
                        operation="remove",
                        description=(
                            f"test removal of stale-path guidance for {reference}"
                        ),
                        removed_text=stale_fragment,
                        estimated_tokens=estimate_tokens(stale_fragment),
                        confidence=0.65,
                        signal="stale_reference",
                    )
                )
        if finding.category == "potential_scope" and finding.paths:
            scoped_source = by_path.get(finding.paths[0])
            scope_match = re.search(r"`([^`/]+)/`", finding.message)
            if scoped_source is None or scope_match is None:
                continue
            target_scope = scope_match.group(1)
            scoped_fragment = next(
                (
                    item
                    for item in _fragments(scoped_source.content)
                    if f"{target_scope}/" in item
                ),
                None,
            )
            if scoped_fragment is None or (
                selected and scoped_source.path not in selected
            ):
                continue
            target_name = (
                "CLAUDE.md" if scoped_source.kind == "claude_md" else "AGENTS.md"
            )
            edits.append(
                MinimizationEdit(
                    path=scoped_source.path,
                    operation="scope",
                    description=f"test moving subtree guidance to {target_scope}/",
                    removed_text=scoped_fragment,
                    estimated_tokens=estimate_tokens(scoped_fragment),
                    replacement_path=f"{target_scope}/{target_name}",
                    replacement_text=scoped_fragment,
                    confidence=0.55,
                    signal="single_subtree_reference",
                )
            )
    unique: dict[tuple[str, str], MinimizationEdit] = {}
    for edit in edits:
        key = (edit.path, edit.removed_text)
        existing = unique.get(key)
        if existing is None or edit.priority > existing.priority:
            unique[key] = edit
    prioritized = sorted(
        unique.values(),
        key=lambda edit: (-edit.priority, edit.operation, edit.path),
    )
    return tuple(prioritized[:max_candidates])


def _candidate_from_edits(
    scan: RepositoryScan,
    edits: tuple[MinimizationEdit, ...],
) -> MinimizationCandidate:
    contents = {source.path: source.content for source in scan.sources}
    for edit in edits:
        current = contents.get(edit.path)
        if current is None:
            continue
        contents[edit.path] = _remove_fragment(current, edit.removed_text)
        if edit.replacement_path and edit.replacement_text:
            existing = contents.get(edit.replacement_path, "")
            separator = "\n\n" if existing.strip() else ""
            contents[edit.replacement_path] = (
                existing.rstrip() + separator + edit.replacement_text.strip() + "\n"
            )
    candidate_scan = scan_repository_files(
        Path(scan.root),
        contents,
        known_paths=scan.known_paths | frozenset(contents),
    )
    return MinimizationCandidate(scan, candidate_scan.sources, edits)


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
    for path in sorted(set(before) | set(after)):
        original = before.get(path, "")
        updated = after.get(path, "")
        if original == updated:
            continue
        lines.extend(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{path}" if path in before else "/dev/null",
                tofile=f"b/{path}" if path in after else "/dev/null",
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
