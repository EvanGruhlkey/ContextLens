"""Stable, serializable reporting model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

from contextlens.analysis.paired import PairedEffect
from contextlens.experiments.model import ReplayResult
from contextlens.experiments.search import SearchReport
from contextlens.optimization.model import VerifiedConfiguration
from contextlens.optimization.predictor import ValuePrediction
from contextlens.profiler.profile import ProfileReport


@dataclass(frozen=True, slots=True)
class Finding:
    """One context finding from observed, predicted, or verified evidence."""

    source_id: str
    name: str
    kind: str
    evidence_level: str
    verdict: str
    tokens: int = 0
    effect: float | None = None
    confidence_low: float | None = None
    confidence_high: float | None = None
    tokens_saved: float | None = None
    cost_saved_usd: float | None = None
    quality_per_1k_tokens: float | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ExperimentNode:
    """Compact representation of one adaptive-search tree node."""

    group_id: str
    parent_id: str | None
    depth: int
    source_ids: tuple[str, ...]
    decision: str
    quality_delta: float | None
    removed_tokens: int
    reason: str


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Compact replay evidence without embedding raw model output."""

    run_id: str
    task_id: str
    variant_id: str
    status: str
    duration_seconds: float
    context_tokens: int
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    removed_source_ids: tuple[str, ...]
    changed_files: tuple[str, ...]
    test_results: tuple[str, ...]
    error: str | None


@dataclass(frozen=True, slots=True)
class Report:
    """Portable ContextLens report."""

    title: str
    generated_at: str
    findings: tuple[Finding, ...] = ()
    experiment_tree: tuple[ExperimentNode, ...] = ()
    runs: tuple[RunRecord, ...] = ()
    warnings: tuple[str, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "generated_at": self.generated_at,
            "summary": dict(self.summary),
            "findings": [asdict(item) for item in self.findings],
            "experiment_tree": [asdict(item) for item in self.experiment_tree],
            "runs": [asdict(item) for item in self.runs],
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Report:
        if value.get("schema_version") != "1.0":
            raise ValueError("unsupported report schema")
        return cls(
            schema_version="1.0",
            title=str(value["title"]),
            generated_at=str(value["generated_at"]),
            summary=dict(value.get("summary", {})),
            findings=tuple(
                Finding(**item)
                for item in value.get("findings", ())
            ),
            experiment_tree=tuple(
                ExperimentNode(
                    group_id=str(item["group_id"]),
                    parent_id=(
                        str(item["parent_id"])
                        if item.get("parent_id") is not None
                        else None
                    ),
                    depth=int(item["depth"]),
                    source_ids=tuple(item["source_ids"]),
                    decision=str(item["decision"]),
                    quality_delta=(
                        float(item["quality_delta"])
                        if item.get("quality_delta") is not None
                        else None
                    ),
                    removed_tokens=int(item["removed_tokens"]),
                    reason=str(item["reason"]),
                )
                for item in value.get("experiment_tree", ())
            ),
            runs=tuple(
                RunRecord(
                    run_id=str(item["run_id"]),
                    task_id=str(item["task_id"]),
                    variant_id=str(item["variant_id"]),
                    status=str(item["status"]),
                    duration_seconds=float(item["duration_seconds"]),
                    context_tokens=int(item["context_tokens"]),
                    input_tokens=(
                        int(item["input_tokens"])
                        if item.get("input_tokens") is not None
                        else None
                    ),
                    output_tokens=(
                        int(item["output_tokens"])
                        if item.get("output_tokens") is not None
                        else None
                    ),
                    cost_usd=(
                        float(item["cost_usd"])
                        if item.get("cost_usd") is not None
                        else None
                    ),
                    removed_source_ids=tuple(item["removed_source_ids"]),
                    changed_files=tuple(item["changed_files"]),
                    test_results=tuple(item["test_results"]),
                    error=(
                        str(item["error"])
                        if item.get("error") is not None
                        else None
                    ),
                )
                for item in value.get("runs", ())
            ),
            warnings=tuple(str(item) for item in value.get("warnings", ())),
            metadata=dict(value.get("metadata", {})),
        )


class ReportBuilder:
    """Combine evidence from the ContextLens pipeline."""

    def __init__(self, title: str = "ContextLens report") -> None:
        self.title = title
        self._findings: dict[tuple[str, str], Finding] = {}
        self._tree: list[ExperimentNode] = []
        self._runs: list[RunRecord] = []
        self._warnings: list[str] = []
        self._summary: dict[str, Any] = {}
        self._metadata: dict[str, Any] = {}

    def add_profile(self, report: ProfileReport) -> ReportBuilder:
        for profile in report.profiles:
            self._findings[("observed", profile.source_id)] = Finding(
                source_id=profile.source_id,
                name=profile.name,
                kind=profile.kind,
                evidence_level="observed",
                verdict=profile.label.value,
                tokens=profile.token_count,
                detail="apparent utilization from one completed run",
            )
        self._summary["profiled_sources"] = len(report.profiles)
        self._summary["profiled_tokens"] = report.total_tokens
        return self

    def add_predictions(
        self,
        predictions: tuple[ValuePrediction, ...],
    ) -> ReportBuilder:
        for prediction in predictions:
            observed = self._findings.get(("observed", prediction.source_id))
            verdict = (
                "likely_helpful"
                if prediction.predicted_effect > 0
                else "likely_harmful"
                if prediction.predicted_effect < 0
                else "uncertain"
            )
            self._findings[("predicted", prediction.source_id)] = Finding(
                source_id=prediction.source_id,
                name=observed.name if observed else prediction.source_id,
                kind=observed.kind if observed else "unknown",
                evidence_level="predicted",
                verdict=verdict,
                tokens=observed.tokens if observed else 0,
                effect=prediction.predicted_effect,
                confidence_low=prediction.predicted_effect
                - prediction.uncertainty,
                confidence_high=prediction.predicted_effect
                + prediction.uncertainty,
                detail="learned estimate; requires target-model verification",
            )
        return self

    def add_effect(
        self,
        effect: PairedEffect,
        *,
        source_id: str | None = None,
        name: str | None = None,
        kind: str = "group",
    ) -> ReportBuilder:
        identifier = source_id or effect.ablated_variant_id
        evidence_level = (
            "verified"
            if effect.evidence_scope.value == "target_model"
            else "screening"
        )
        self._findings[("verified", identifier)] = Finding(
            source_id=identifier,
            name=name or identifier,
            kind=kind,
            evidence_level=evidence_level,
            verdict=effect.verdict.value,
            effect=effect.effect,
            confidence_low=effect.confidence_low,
            confidence_high=effect.confidence_high,
            tokens_saved=effect.input_tokens_saved_by_ablation,
            cost_saved_usd=effect.cost_saved_by_ablation_usd,
            quality_per_1k_tokens=effect.quality_per_1k_tokens,
            detail=(
                f"{effect.pair_count} paired trial(s) across "
                f"{effect.task_count} task(s)"
            ),
        )
        self._warnings.extend(effect.warnings)
        return self

    def add_search(self, report: SearchReport) -> ReportBuilder:
        self._tree.extend(
            ExperimentNode(
                group_id=node.group.group_id,
                parent_id=node.group.parent_id,
                depth=node.group.depth,
                source_ids=node.group.source_ids,
                decision=node.decision.value,
                quality_delta=node.quality_delta,
                removed_tokens=node.group.removed_tokens,
                reason=node.reason,
            )
            for node in report.nodes
        )
        self._summary.update(
            {
                "experiments_planned": report.experiments_planned,
                "planned_context_tokens": report.planned_context_tokens,
                "estimated_experiment_cost_usd": report.estimated_cost_usd,
                "search_stopping_reason": report.stopping_reason,
                "recommended_removals": list(report.recommended_removals),
            }
        )
        return self

    def add_verified_configuration(
        self,
        verified: VerifiedConfiguration,
    ) -> ReportBuilder:
        self._summary.update(
            {
                "candidate_id": verified.candidate.candidate_id,
                "candidate_accepted": verified.accepted,
                "candidate_removed_sources": list(
                    verified.candidate.removed_source_ids
                ),
                "candidate_retained_tokens": verified.candidate.retained_tokens,
                "candidate_removed_tokens": verified.candidate.removed_tokens,
                "candidate_quality_change": verified.quality_change,
                "objective": verified.candidate.objective.value,
                "objective_improvement": verified.objective_improvement,
                "rejection_reasons": list(verified.rejection_reasons),
            }
        )
        if not verified.accepted:
            self._warnings.extend(verified.rejection_reasons)
        return self

    def add_runs(
        self,
        results: tuple[ReplayResult, ...],
    ) -> ReportBuilder:
        for result in results:
            outcome = result.outcome
            self._runs.append(
                RunRecord(
                    run_id=result.run_id,
                    task_id=result.task_id,
                    variant_id=result.variant_id,
                    status=result.status.value,
                    duration_seconds=result.duration_seconds,
                    context_tokens=result.context_tokens,
                    input_tokens=(
                        outcome.input_tokens if outcome is not None else None
                    ),
                    output_tokens=(
                        outcome.output_tokens if outcome is not None else None
                    ),
                    cost_usd=outcome.cost_usd if outcome is not None else None,
                    removed_source_ids=result.removed_source_ids,
                    changed_files=tuple(
                        change.path
                        for change in result.file_changes
                    ),
                    test_results=(
                        outcome.test_results if outcome is not None else ()
                    ),
                    error=result.error,
                )
            )
        return self

    def metadata(self, **values: Any) -> ReportBuilder:
        self._metadata.update(values)
        return self

    def build(self) -> Report:
        findings = sorted(
            self._findings.values(),
            key=lambda item: (
                _evidence_order(item.evidence_level),
                _verdict_order(item.verdict),
                -(abs(item.effect) if item.effect is not None else 0),
                -item.tokens,
                item.name.casefold(),
            ),
        )
        return Report(
            title=self.title,
            generated_at=datetime.now(UTC).isoformat(),
            findings=tuple(findings),
            experiment_tree=tuple(self._tree),
            runs=tuple(self._runs),
            warnings=tuple(dict.fromkeys(self._warnings)),
            summary=self._summary,
            metadata=self._metadata,
        )


def _evidence_order(value: str) -> int:
    return {
        "target_model": 0,
        "verified": 0,
        "screening": 1,
        "predicted": 2,
        "observed": 3,
    }.get(value, 4)


def _verdict_order(value: str) -> int:
    return {
        "helpful": 0,
        "harmful": 1,
        "neutral": 2,
        "likely_helpful": 3,
        "likely_harmful": 4,
        "duplicated": 5,
        "unused": 6,
        "uncertain": 7,
        "used": 8,
    }.get(value, 9)
