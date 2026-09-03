from __future__ import annotations

import ast
from pathlib import Path
from textwrap import dedent

from contextlens.pruning import (
    ContextPruner,
    LineReason,
    ObservationKind,
    PruneRequest,
    ReceiptStore,
    SemanticScores,
)


class _Scorer:
    backend_id = "fixture-v1"

    def __init__(self, lines: dict[int, float], *, broken: bool = False) -> None:
        self.lines = lines
        self.broken = broken
        self.calls = 0

    def score(self, request: PruneRequest) -> SemanticScores:
        self.calls += 1
        if self.broken:
            raise RuntimeError("offline")
        return SemanticScores(self.backend_id, self.lines)


def _source() -> str:
    return dedent(
        """\
        from transport import BaseTransport
        from retry import RetryConfig

        class OAuthClient(BaseTransport):
            def refresh(self):
                try:
                    return self.transport.request(
                        "/token",
                        options=RetryConfig(timeout=30),
                    )
                except TimeoutError:
                    return None

        def unrelated():
            return "noise"
            # noise 01
            # noise 02
            # noise 03
            # noise 04
            # noise 05
            # noise 06
            # noise 07
            # noise 08
            # noise 09
            # noise 10
            # noise 11
            # noise 12
            # noise 13
            # noise 14
            # noise 15
            # noise 16
            # noise 17
            # noise 18
            # noise 19
            # noise 20
        """
    )


def test_pipeline_combines_semantic_and_dependency_evidence(tmp_path: Path) -> None:
    scorer = _Scorer({9: 0.95, 15: 0.1})
    pruner = ContextPruner(scorer, ReceiptStore(tmp_path))
    request = PruneRequest(
        task="Fix refresh timeout",
        focus="Where are request options selected?",
        content=_source(),
        language="python",
        minimum_tokens=0,
        context_radius=0,
    )

    result = pruner.prune(request)

    ast.parse(result.text)
    reasons = {item.line_number: item.reasons for item in result.decisions}
    assert reasons[9] == (LineReason.SEMANTIC,)
    assert LineReason.DEPENDENCY in reasons[1]
    assert LineReason.DEPENDENCY in reasons[2]
    assert LineReason.SCOPE in reasons[4]
    assert LineReason.CONTROL_FLOW in reasons[11]
    assert "unrelated" not in result.text
    assert result.receipt_id in result.text
    assert result.retained_tokens < result.original_tokens
    assert pruner.receipts.read(result.receipt_id) == request.content


def test_pipeline_uses_query_gate_for_independent_dependency_scores(
    tmp_path: Path,
) -> None:
    class LayeredScorer:
        backend_id = "layered-v1"

        def score(self, request: PruneRequest) -> SemanticScores:
            return SemanticScores(
                self.backend_id,
                {9: 0.95, 15: 0.2},
                dependency_scores={1: 0.98, 2: 0.96},
                semantic_weight=0.45,
            )

    request = PruneRequest(
        task="Fix refresh timeout",
        content=_source(),
        minimum_tokens=0,
        context_radius=0,
        threshold=0.4,
    )

    result = ContextPruner(
        LayeredScorer(), ReceiptStore(tmp_path / "layered")
    ).prune(request)
    decisions = {item.line_number: item for item in result.decisions}

    assert decisions[9].reasons == (LineReason.SEMANTIC,)
    assert LineReason.DEPENDENCY in decisions[1].reasons
    assert decisions[1].dependency_score == 0.98
    assert decisions[1].combined_score > request.threshold
    assert 15 not in decisions


def test_pipeline_keeps_original_when_no_line_clears_gate(tmp_path: Path) -> None:
    request = PruneRequest(
        task="Inspect",
        content=_source(),
        minimum_tokens=0,
        threshold=0.8,
    )

    result = ContextPruner(
        _Scorer({4: 0.2}), ReceiptStore(tmp_path / "empty")
    ).prune(request)

    assert result.text == request.content
    assert result.bypass_reason == "no_relevant_lines"


def test_pipeline_adds_only_local_context_not_a_global_rubric(tmp_path: Path) -> None:
    scorer = _Scorer({3: 0.8})
    source = (
        "x = 1\ny = 2\nprint(y)\nz = 4\nq = 5\n"
        + "\n".join(f"unused_{index} = {index}" for index in range(30))
        + "\n"
    )
    request = PruneRequest(
        task="Find output",
        content=source,
        minimum_tokens=0,
        context_radius=1,
    )

    result = ContextPruner(scorer, ReceiptStore(tmp_path)).prune(request)
    reasons = {item.line_number: item.reasons for item in result.decisions}

    assert LineReason.LOCAL_CONTEXT in reasons[2]
    assert LineReason.LOCAL_CONTEXT in reasons[4]
    assert 5 not in reasons


def test_short_observation_bypasses_scoring(tmp_path: Path) -> None:
    scorer = _Scorer({1: 1.0})
    request = PruneRequest(task="Inspect", content="small")

    result = ContextPruner(scorer, ReceiptStore(tmp_path)).prune(request)

    assert result.text == "small"
    assert result.bypass_reason == "below_minimum_tokens"
    assert scorer.calls == 0


def test_backend_and_parse_failures_return_original(tmp_path: Path) -> None:
    broken = ContextPruner(_Scorer({}, broken=True), ReceiptStore(tmp_path / "one"))
    request = PruneRequest(task="Inspect", content=_source(), minimum_tokens=0)
    assert broken.prune(request).bypass_reason == "semantic_backend_error"

    invalid = ContextPruner(_Scorer({1: 1.0}), ReceiptStore(tmp_path / "two"))
    request = PruneRequest(task="Inspect", content="def bad(:\n", minimum_tokens=0)
    result = invalid.prune(request)
    assert result.text == request.content
    assert result.bypass_reason == "source_parse_error"


def test_unsupported_observations_return_original(tmp_path: Path) -> None:
    scorer = _Scorer({1: 1.0})
    pruner = ContextPruner(scorer, ReceiptStore(tmp_path))
    request = PruneRequest(
        task="Inspect",
        content="{\"large\": true}",
        kind=ObservationKind.JSON,
        minimum_tokens=0,
    )

    result = pruner.prune(request)

    assert result.bypass_reason == "unsupported_kind"
    assert scorer.calls == 0
