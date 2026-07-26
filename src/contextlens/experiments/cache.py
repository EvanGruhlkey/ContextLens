"""Replay result cache contracts and an in-memory implementation."""

from __future__ import annotations

from threading import Lock
from typing import Protocol

from contextlens.experiments.model import ReplayResult


class ReplayCache(Protocol):
    """Store completed replay evidence by content-derived cache key."""

    def get(self, key: str) -> ReplayResult | None:
        """Return a cached result when available."""

    def put(self, key: str, result: ReplayResult) -> None:
        """Store a completed result."""


class MemoryReplayCache:
    """Thread-safe replay cache for one coordinator process."""

    def __init__(self) -> None:
        self._values: dict[str, ReplayResult] = {}
        self._lock = Lock()

    def get(self, key: str) -> ReplayResult | None:
        with self._lock:
            return self._values.get(key)

    def put(self, key: str, result: ReplayResult) -> None:
        with self._lock:
            self._values[key] = result

