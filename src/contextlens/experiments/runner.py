"""Isolated replay worker and bounded parallel coordinator."""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from uuid import uuid4

from contextlens.experiments.adapters import AgentAdapter
from contextlens.experiments.cache import ReplayCache
from contextlens.experiments.model import (
    AgentSettings,
    ContextVariant,
    ReplayRequest,
    ReplayResult,
    ReplayStatus,
    ReplayTask,
    ResourceLimits,
)
from contextlens.experiments.workspace import DirectorySnapshot, compare_workspace
from contextlens.trace.model import ContextSource


class ReplayWorker:
    """Execute one context variant in a fresh workspace."""

    def __init__(
        self,
        *,
        adapter: AgentAdapter,
        snapshot: DirectorySnapshot,
        task: ReplayTask,
        context: tuple[ContextSource, ...],
        settings: AgentSettings,
        timeout_seconds: float,
    ) -> None:
        self.adapter = adapter
        self.snapshot = snapshot
        self.task = task
        self.context = context
        self.settings = settings
        self.timeout_seconds = timeout_seconds
        source_ids = [source.source_id for source in context]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("context source IDs must be unique")

    def run(self, variant: ContextVariant, *, attempt: int = 1) -> ReplayResult:
        known_ids = {source.source_id for source in self.context}
        unknown = variant.removed_source_ids - known_ids
        if unknown:
            raise ValueError(f"variant removes unknown source IDs: {sorted(unknown)}")
        selected = tuple(
            source
            for source in self.context
            if source.source_id not in variant.removed_source_ids
        )
        run_id = str(uuid4())
        started = time.monotonic()
        with self.snapshot.isolated() as (workspace, before):
            request = ReplayRequest(
                run_id=run_id,
                task=self.task,
                variant=variant,
                context=selected,
                settings=self.settings,
                workspace=str(workspace),
                timeout_seconds=self.timeout_seconds,
            )
            try:
                outcome = self.adapter.run(request)
                status = ReplayStatus.COMPLETED
                error = None
            except TimeoutError as exception:
                outcome = None
                status = ReplayStatus.TIMED_OUT
                error = str(exception)
            except Exception as exception:
                outcome = None
                status = ReplayStatus.FAILED
                error = f"{type(exception).__name__}: {exception}"
            changes = compare_workspace(workspace, before)
        return ReplayResult(
            run_id=run_id,
            task_id=self.task.task_id,
            variant_id=variant.variant_id,
            removed_source_ids=tuple(sorted(variant.removed_source_ids)),
            status=status,
            attempt=attempt,
            duration_seconds=time.monotonic() - started,
            context_source_ids=tuple(source.source_id for source in selected),
            context_tokens=sum(_source_tokens(source) for source in selected),
            outcome=outcome,
            file_changes=changes,
            error=error,
            cache_key=self.cache_key(variant),
        )

    def cache_key(self, variant: ContextVariant) -> str:
        value = {
            "adapter": self.adapter.adapter_id,
            "workspace": self.snapshot.digest,
            "task": {
                "id": self.task.task_id,
                "instruction": self.task.instruction,
                "metadata": dict(self.task.metadata),
            },
            "settings": {
                "provider": self.settings.provider,
                "model": self.settings.model,
                "seed": self.settings.seed,
                "temperature": self.settings.temperature,
                "tools": self.settings.tools,
                "parameters": dict(self.settings.parameters),
            },
            "context": [
                source.to_dict()
                for source in self.context
                if source.source_id not in variant.removed_source_ids
            ],
        }
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class ReplayCoordinator:
    """Run bounded context variants concurrently with retry control."""

    def __init__(
        self,
        worker: ReplayWorker,
        limits: ResourceLimits,
        *,
        cache: ReplayCache | None = None,
    ) -> None:
        self.worker = worker
        self.limits = limits
        self.cache = cache

    def run(self, variants: tuple[ContextVariant, ...]) -> tuple[ReplayResult, ...]:
        self._preflight(variants)
        results: dict[int, ReplayResult] = {}
        with ThreadPoolExecutor(max_workers=self.limits.max_workers) as executor:
            pending = {
                executor.submit(self._with_retries, variant): index
                for index, variant in enumerate(variants)
            }
            for future in as_completed(pending):
                results[pending[future]] = future.result()
        return tuple(results[index] for index in range(len(variants)))

    def _with_retries(self, variant: ContextVariant) -> ReplayResult:
        key = self.worker.cache_key(variant)
        if self.cache is not None:
            cached = self.cache.get(key)
            if cached is not None:
                return replace(
                    cached,
                    variant_id=variant.variant_id,
                    removed_source_ids=tuple(sorted(variant.removed_source_ids)),
                    status=ReplayStatus.CACHED,
                )
        result: ReplayResult | None = None
        for attempt in range(1, self.limits.retries + 2):
            result = self.worker.run(variant, attempt=attempt)
            if result.status is ReplayStatus.COMPLETED:
                if self.cache is not None:
                    self.cache.put(key, result)
                return result
        assert result is not None
        return result

    def _preflight(self, variants: tuple[ContextVariant, ...]) -> None:
        maximum_attempts = len(variants) * (self.limits.retries + 1)
        if maximum_attempts > self.limits.max_runs:
            raise ValueError(
                f"{maximum_attempts} possible attempts exceed "
                f"max_runs={self.limits.max_runs}"
            )
        if len({variant.variant_id for variant in variants}) != len(variants):
            raise ValueError("variant IDs must be unique")
        context_tokens = sum(
            sum(
                _source_tokens(source)
                for source in self.worker.context
                if source.source_id not in variant.removed_source_ids
            )
            for variant in variants
        )
        if (
            self.limits.max_context_tokens is not None
            and context_tokens > self.limits.max_context_tokens
        ):
            raise ValueError(
                f"planned context tokens {context_tokens} exceed "
                f"max_context_tokens={self.limits.max_context_tokens}"
            )
        estimated_costs = [variant.estimated_cost_usd for variant in variants]
        if (
            self.limits.max_estimated_cost_usd is not None
            and any(cost is None for cost in estimated_costs)
        ):
            raise ValueError("every variant needs an estimated cost under a cost limit")
        total_cost = sum(cost or 0.0 for cost in estimated_costs)
        if (
            self.limits.max_estimated_cost_usd is not None
            and total_cost > self.limits.max_estimated_cost_usd
        ):
            raise ValueError(
                f"estimated cost ${total_cost:.4f} exceeds "
                f"limit ${self.limits.max_estimated_cost_usd:.4f}"
            )


def _source_tokens(source: ContextSource) -> int:
    if source.token_count is not None:
        return source.token_count
    if source.content is None:
        return 0
    return (len(source.content.encode("utf-8")) + 3) // 4
