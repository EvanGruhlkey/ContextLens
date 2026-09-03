from __future__ import annotations

import pytest

from contextlens.pruning import (
    LineDecision,
    LineReason,
    ObservationKind,
    OmittedRange,
    PruneRequest,
    PruneResult,
)


def test_request_creates_self_contained_goal_from_task_and_focus() -> None:
    request = PruneRequest(
        task="Fix refresh behavior",
        content="def retry(): pass",
        focus="Where is retry delay selected?",
        tool="read_file",
        arguments={"path": "src/client.py", "nested": {"ignored": True}},
        language="python",
    )

    assert request.kind is ObservationKind.CODE
    assert request.goal_hint == (
        "For the coding task 'Fix refresh behavior', what code in src/client.py "
        "is needed to answer: Where is retry delay selected?"
    )
    assert request.query == request.goal_hint
    assert len(request.content_hash) == 64


def test_request_creates_goal_when_focus_is_not_supplied() -> None:
    request = PruneRequest(
        task="Fix refresh behavior.",
        content="def retry(): pass",
        arguments={"path": "src/client.py"},
    )

    assert request.goal_hint == (
        "What code in src/client.py is needed to complete the coding task: "
        "Fix refresh behavior?"
    )


def test_request_rejects_invalid_controls() -> None:
    with pytest.raises(ValueError, match="task"):
        PruneRequest(task=" ", content="x")
    with pytest.raises(ValueError, match="threshold"):
        PruneRequest(task="x", content="x", threshold=1.1)
    with pytest.raises(ValueError, match="dependency_hops"):
        PruneRequest(task="x", content="x", dependency_hops=-1)


def test_result_reports_distinct_retention_reasons() -> None:
    decision = LineDecision(
        line_number=3,
        semantic_score=0.91,
        reasons=(LineReason.SEMANTIC, LineReason.DEPENDENCY),
    )
    result = PruneResult(
        text="import time",
        receipt_id="cl_example",
        content_hash="a" * 64,
        backend="fixture",
        decisions=(decision,),
        omitted_ranges=(OmittedRange(1, 2),),
        original_lines=3,
        retained_lines=1,
        original_tokens=20,
        retained_tokens=5,
        latency_ms=2.5,
    )

    assert result.saved_tokens == 15
    assert result.reduction_fraction == 0.75
    assert result.to_dict()["kept_lines"][0]["reasons"] == [
        "semantic",
        "dependency",
    ]
