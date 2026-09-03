"""Semantic scoring backends."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from contextlens.pruning.model import PruneRequest


@dataclass(frozen=True, slots=True)
class SemanticScores:
    """Independent semantic and dependency evidence returned by a backend."""

    backend: str
    line_scores: Mapping[int, float]
    document_score: float | None = None
    input_tokens: int | None = None
    latency_ms: float = 0.0
    dependency_scores: Mapping[int, float] = field(default_factory=dict)
    semantic_weight: float = 1.0

    def __post_init__(self) -> None:
        normalized = _validated_scores(self.line_scores)
        dependencies = _validated_scores(self.dependency_scores)
        if self.document_score is not None and not 0 <= self.document_score <= 1:
            raise ValueError("document_score must be between zero and one")
        if self.input_tokens is not None and self.input_tokens < 0:
            raise ValueError("input_tokens cannot be negative")
        if self.latency_ms < 0:
            raise ValueError("latency cannot be negative")
        if not 0 <= self.semantic_weight <= 1:
            raise ValueError("semantic_weight must be between zero and one")
        object.__setattr__(self, "line_scores", normalized)
        object.__setattr__(self, "dependency_scores", dependencies)


class SemanticScorer(Protocol):
    """Score observation lines against the current query."""

    @property
    def backend_id(self) -> str:
        """Return a stable backend identifier."""

    def score(self, request: PruneRequest) -> SemanticScores:
        """Return semantic scores for covered lines."""


class HttpSemanticScorer:
    """Call a query-conditioned line-pruning sidecar."""

    def __init__(
        self,
        url: str = "http://127.0.0.1:8000/prune",
        *,
        timeout_seconds: float = 60.0,
        backend_id: str = "swe-pruner-http-v1",
    ) -> None:
        if not url.startswith(("http://", "https://")):
            raise ValueError("url must use http or https")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.url = url
        self.timeout_seconds = timeout_seconds
        self._backend_id = backend_id

    @property
    def backend_id(self) -> str:
        return self._backend_id

    def score(self, request: PruneRequest) -> SemanticScores:
        started = time.perf_counter()
        body = json.dumps(
            {
                "query": request.query,
                "code": request.content,
                "threshold": request.threshold,
                "always_keep_first_frags": False,
                "chunk_overlap_tokens": 50,
            }
        ).encode("utf-8")
        message = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                message,
                timeout=self.timeout_seconds,
            ) as response:
                raw = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(f"semantic backend unavailable: {error}") from error
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RuntimeError("semantic backend returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise RuntimeError("semantic backend response must be an object")
        error_message = payload.get("error_msg")
        if error_message:
            raise RuntimeError(f"semantic backend declined request: {error_message}")
        raw_semantic = payload.get("semantic_scores")
        if raw_semantic is None:
            kept = _positive_lines(payload.get("kept_frags"))
            semantic = {line: 1.0 for line in kept}
        else:
            semantic = _transport_scores(raw_semantic, "semantic_scores")
        semantic_weight = _optional_score(payload.get("semantic_weight"))
        return SemanticScores(
            backend=self.backend_id,
            line_scores=semantic,
            document_score=_optional_score(payload.get("score")),
            input_tokens=_optional_int(payload.get("model_input_token_cnt")),
            latency_ms=(time.perf_counter() - started) * 1000,
            dependency_scores=_transport_scores(
                payload.get("dependency_scores", {}),
                "dependency_scores",
            ),
            semantic_weight=1.0 if semantic_weight is None else semantic_weight,
        )


def _validated_scores(value: Mapping[int, float]) -> dict[int, float]:
    normalized: dict[int, float] = {}
    for line, score in value.items():
        if line < 1:
            raise ValueError("line numbers must be positive")
        if not 0 <= score <= 1:
            raise ValueError("line scores must be between zero and one")
        normalized[int(line)] = float(score)
    return normalized


def _transport_scores(value: Any, field_name: str) -> dict[int, float]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{field_name} must be an object")
    result: dict[int, float] = {}
    for raw_line, raw_score in value.items():
        try:
            line = int(raw_line)
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"{field_name} contains an invalid line") from error
        if not isinstance(raw_score, int | float) or isinstance(raw_score, bool):
            raise RuntimeError(f"{field_name} contains an invalid score")
        score = float(raw_score)
        if line < 1 or not 0 <= score <= 1:
            raise RuntimeError(f"{field_name} contains an invalid score")
        result[line] = score
    return result


def _positive_lines(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise RuntimeError("semantic backend omitted kept_frags")
    lines: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool) or item < 1:
            raise RuntimeError("semantic backend returned an invalid line number")
        lines.append(item)
    return tuple(dict.fromkeys(lines))


def _optional_score(value: Any) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise RuntimeError("semantic backend returned an invalid score")
    score = float(value)
    if not 0 <= score <= 1:
        raise RuntimeError("semantic backend returned an out-of-range score")
    return score


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError("semantic backend returned an invalid token count")
    return value
