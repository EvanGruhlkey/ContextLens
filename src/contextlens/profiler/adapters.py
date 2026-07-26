"""Optional extension contracts for provider-specific profiler signals."""

from __future__ import annotations

from typing import Protocol

from contextlens.profiler.model import RunObservation, UsageSignal
from contextlens.trace.model import ContextEvent


class ContentSimilarity(Protocol):
    """Score similarity between two context payloads from zero to one."""

    def score(self, left: str, right: str) -> float:
        """Return zero for unrelated content and one for equivalent content."""


class ModelInternalsAdapter(Protocol):
    """Extract optional model-specific signals from an already completed run."""

    def signals(
        self,
        event: ContextEvent,
        observation: RunObservation,
    ) -> tuple[UsageSignal, ...]:
        """Return log-probability, attention, gradient, or related signals."""

