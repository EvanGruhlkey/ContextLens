from __future__ import annotations

from pathlib import Path

import pytest

from contextlens.pruning import PruneRequest, ReceiptStore, SemanticScores
from contextlens.pruning.server import PruningService


class _Scorer:
    backend_id = "fixture-v1"

    def score(self, request: PruneRequest) -> SemanticScores:
        return SemanticScores(self.backend_id, {2: 0.99})


def _source() -> str:
    lines = ["VALUE = 1", "print(VALUE)"]
    lines.extend(f"unused_{index} = {index}" for index in range(40))
    return "\n".join(lines) + "\n"


def test_service_prunes_and_recovers_exact_source(tmp_path: Path) -> None:
    service = PruningService(_Scorer(), ReceiptStore(tmp_path / "receipts"))

    result = service.prune(
        {
            "task": "Find the displayed value",
            "content": _source(),
            "minimum_tokens": 0,
            "context_radius": 0,
        }
    )
    recovered = service.recover({"receipt_id": result["receipt_id"]})

    assert result["backend"] == "fixture-v1"
    assert result["retained_tokens"] < result["original_tokens"]
    assert recovered["content"] == _source()


def test_service_recovers_a_line_range(tmp_path: Path) -> None:
    service = PruningService(_Scorer(), ReceiptStore(tmp_path / "receipts"))
    result = service.prune(
        {"task": "Inspect", "content": _source(), "minimum_tokens": 0}
    )

    recovered = service.recover(
        {
            "receipt_id": result["receipt_id"],
            "start_line": 1,
            "end_line": 2,
        }
    )

    assert recovered["content"] == "VALUE = 1\nprint(VALUE)\n"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"content": "x = 1"}, "task"),
        ({"task": "Inspect"}, "content"),
        ({"task": "Inspect", "content": "x = 1", "threshold": True}, "number"),
        ({"task": "Inspect", "content": "x = 1", "arguments": []}, "object"),
    ],
)
def test_service_rejects_invalid_payloads(
    tmp_path: Path,
    payload: dict[str, object],
    message: str,
) -> None:
    service = PruningService(_Scorer(), ReceiptStore(tmp_path / "receipts"))

    with pytest.raises((TypeError, ValueError), match=message):
        service.prune(payload)
