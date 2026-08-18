"""Deterministic CI gates for static and verified context changes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contextlens.regression import VerificationReport
from contextlens.repository import RepositoryDiff


@dataclass(frozen=True, slots=True)
class StaticCiPolicy:
    """Optional footprint thresholds; omitted thresholds are report-only."""

    max_context_increase_fraction: float | None = None
    max_duplicate_increase_tokens: int | None = None
    max_stale_reference_increase: int | None = None

    def __post_init__(self) -> None:
        if (
            self.max_context_increase_fraction is not None
            and self.max_context_increase_fraction < 0
        ):
            raise ValueError("max context increase cannot be negative")
        if (
            self.max_duplicate_increase_tokens is not None
            and self.max_duplicate_increase_tokens < 0
        ):
            raise ValueError("max duplicate increase cannot be negative")
        if (
            self.max_stale_reference_increase is not None
            and self.max_stale_reference_increase < 0
        ):
            raise ValueError("max stale-reference increase cannot be negative")


@dataclass(frozen=True, slots=True)
class CiResult:
    """Machine-readable CI outcome with stable exit semantics."""

    passed: bool
    mode: str
    reasons: tuple[str, ...]
    report: dict[str, Any]

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 4

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "report_type": "contextlens_ci",
            "mode": self.mode,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "report": self.report,
        }


def evaluate_static_ci(
    report: RepositoryDiff,
    policy: StaticCiPolicy,
    *,
    effective_context: dict[str, Any] | None = None,
) -> CiResult:
    """Gate deterministic footprint deltas without asserting causal impact."""

    reasons: list[str] = []
    if (
        policy.max_context_increase_fraction is not None
        and report.base.total_tokens == 0
        and report.candidate.total_tokens > 0
    ):
        reasons.append(
            f"context footprint increased from zero to "
            f"{report.candidate.total_tokens:,} estimated tokens"
        )
    if (
        policy.max_context_increase_fraction is not None
        and report.change_fraction is not None
        and report.change_fraction > policy.max_context_increase_fraction
    ):
        reasons.append(
            f"context footprint increased {report.change_fraction:.1%}, above "
            f"the {policy.max_context_increase_fraction:.1%} limit"
        )
    if (
        policy.max_duplicate_increase_tokens is not None
        and report.duplicate_delta > policy.max_duplicate_increase_tokens
    ):
        reasons.append(
            f"duplicate context increased {report.duplicate_delta:,} tokens, above "
            f"the {policy.max_duplicate_increase_tokens:,}-token limit"
        )
    if (
        policy.max_stale_reference_increase is not None
        and report.stale_reference_delta > policy.max_stale_reference_increase
    ):
        reasons.append(
            f"stale references increased by {report.stale_reference_delta}, above "
            f"the {policy.max_stale_reference_increase} limit"
        )
    report_value = report.to_dict()
    if effective_context is not None:
        report_value["effective_context"] = effective_context
    return CiResult(
        passed=not reasons,
        mode="static",
        reasons=tuple(reasons),
        report=report_value,
    )


def evaluate_verified_ci(report: VerificationReport) -> CiResult:
    """Use the verification engine's fail-closed verdict as the CI gate."""

    passed = report.exit_code == 0
    reasons = () if passed else (report.rationale,)
    return CiResult(
        passed=passed,
        mode="verified",
        reasons=reasons,
        report=report.to_dict(),
    )


def write_summary(path: Path, content: str) -> None:
    """Append a Markdown result to GitHub's step summary or another file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        if not content.endswith("\n"):
            stream.write("\n")


def build_ci_arguments(
    *,
    mode: str,
    base: str,
    config: str = ".contextlens/evals.json",
    provider: str = "portable",
    targets: tuple[str, ...] = (),
    max_context_increase: str = "",
    max_duplicate_increase: str = "",
    max_stale_increase: str = "",
) -> tuple[str, ...]:
    """Build a shell-independent CI argv for integrations and tests."""

    if mode not in {"static", "verified"}:
        raise ValueError("CI mode must be static or verified")
    arguments = [
        "ci",
        "--mode",
        mode,
        "--base",
        base,
        "--provider",
        provider,
        "--json-output",
        ".contextlens/ci-result.json",
    ]
    if mode == "verified":
        arguments.extend(("--config", config))
    for flag, value in (
        ("--max-context-increase", max_context_increase),
        ("--max-duplicate-increase", max_duplicate_increase),
        ("--max-stale-increase", max_stale_increase),
    ):
        if value:
            arguments.extend((flag, value))
    for target in targets:
        arguments.extend(("--target", target))
    return tuple(arguments)
