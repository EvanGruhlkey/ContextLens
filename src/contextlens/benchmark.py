"""Deterministic adaptive-versus-exhaustive benchmark."""

from __future__ import annotations

from dataclasses import dataclass

from contextlens.experiments import (
    AdaptiveAblationPlanner,
    ScoreObservation,
    SearchConfig,
)
from contextlens.trace import ContextSource, SourceKind


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    sources: int
    adaptive_experiments: int
    exhaustive_experiments: int
    experiments_saved: int
    reduction_fraction: float
    correctly_retained_critical_source: bool
    removable_sources_found: int


def run_benchmark(source_count: int = 32) -> BenchmarkResult:
    if source_count < 2 or source_count & (source_count - 1):
        raise ValueError("source_count must be a power of two greater than one")
    critical_id = "source-00"
    context = tuple(
        ContextSource(
            source_id=f"source-{index:02d}",
            kind=SourceKind.TOOL_SCHEMA,
            name=f"tool-{index:02d}",
            content=f"Deterministic tool schema fixture {index}.",
            token_count=100,
            token_count_method="fixture",
        )
        for index in range(source_count)
    )
    planner = AdaptiveAblationPlanner(
        context,
        config=SearchConfig(
            quality_tolerance=0,
            max_experiments=source_count + 1,
            batch_size=8,
        ),
        groups={
            "all-tool-schemas": frozenset(
                source.source_id
                for source in context
            )
        },
    )
    while batch := planner.next_batch():
        for variant in batch:
            score = (
                0.8
                if critical_id in variant.removed_source_ids
                else 1.0
            )
            planner.record(variant.variant_id, ScoreObservation(score))
    report = planner.report()
    exhaustive = source_count + 1
    adaptive = report.experiments_planned
    removals = set(report.recommended_removals)
    return BenchmarkResult(
        sources=source_count,
        adaptive_experiments=adaptive,
        exhaustive_experiments=exhaustive,
        experiments_saved=exhaustive - adaptive,
        reduction_fraction=(exhaustive - adaptive) / exhaustive,
        correctly_retained_critical_source=critical_id not in removals,
        removable_sources_found=len(removals),
    )

