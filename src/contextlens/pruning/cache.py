"""Opt-in process-local score caching with an explicit checkpoint namespace."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import replace

from contextlens.pruning.model import PruneRequest
from contextlens.pruning.scoring import SemanticScorer, SemanticScores


class CachedSemanticScorer:
    """Cache scores only when the caller supplies a model/runtime identity.

    Receipts are never cached. Namespace must change when checkpoint weights or
    inference settings change. A cache hit uses no additional model input tokens.
    """

    def __init__(
        self, scorer: SemanticScorer, namespace: str, capacity: int = 128
    ) -> None:
        if not namespace.strip() or capacity < 1:
            raise ValueError(
                "cache requires a checkpoint namespace and positive capacity"
            )
        self.scorer = scorer
        self.namespace = namespace
        self.capacity = capacity
        self.hits = 0
        self._scores: OrderedDict[str, SemanticScores] = OrderedDict()

    @property
    def backend_id(self) -> str:
        return self.scorer.backend_id

    def score(self, request: PruneRequest) -> SemanticScores:
        identity = {
            "namespace": self.namespace,
            "backend": self.backend_id,
            "hash": request.content_hash,
            "goal": request.goal_hint,
            "threshold": request.threshold,
            "kind": request.kind.value,
            "language": request.language,
        }
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        cached = self._scores.get(key)
        if cached is not None:
            self.hits += 1
            self._scores.move_to_end(key)
            return replace(cached, input_tokens=0, latency_ms=0.0)
        result = self.scorer.score(request)
        self._scores[key] = result
        if len(self._scores) > self.capacity:
            self._scores.popitem(last=False)
        return result
