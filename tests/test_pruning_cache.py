from __future__ import annotations

from contextlens.pruning import PruneRequest, SemanticScores
from contextlens.pruning.cache import CachedSemanticScorer


def test_score_cache_changes_with_goal_content_threshold_and_eviction() -> None:
    class Scorer:
        backend_id = "fixture"
        calls = 0

        def score(self, request: PruneRequest) -> SemanticScores:
            self.calls += 1
            return SemanticScores(self.backend_id, {1: 1.0}, input_tokens=17)

    underlying = Scorer()
    cache = CachedSemanticScorer(
        underlying, "checkpoint-revision:runtime-variant", capacity=2
    )
    request = PruneRequest(task="refresh", content="one")
    assert cache.score(request).input_tokens == 17
    assert cache.score(request).input_tokens == 0
    cache.score(PruneRequest(task="refresh", focus="exceptions", content="one"))
    cache.score(PruneRequest(task="refresh", content="two"))
    cache.score(request)
    assert underlying.calls == 4
    cache.score(PruneRequest(task="refresh", content="one", threshold=0.7))
    assert underlying.calls == 5
    assert cache.hits == 1
