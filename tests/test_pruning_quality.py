from __future__ import annotations

from pathlib import Path

import pytest

from contextlens.pruning import ContextPruner, PruneRequest, ReceiptStore
from contextlens.pruning.structure import close_python_dependencies


@pytest.mark.parametrize("content", ["a\r\nb\r\n", "a\r\nb\nc\r", "a\nb\n"])
def test_receipt_recovery_preserves_original_newlines(
    tmp_path: Path, content: str
) -> None:
    store = ReceiptStore(tmp_path)
    request = PruneRequest(task="Inspect", content=content)
    receipt = store.save(request)
    assert store.read(receipt.receipt_id) == content
    assert store.save(request).receipt_id == receipt.receipt_id
    assert (tmp_path / f"{receipt.receipt_id}.txt").read_bytes() == content.encode()


def test_dependencies_use_lexical_scope() -> None:
    source = (
        "def target():\n    value = 7\n    return value\n"
        "def unrelated():\n    value = 99\n    return value\n"
    )
    result = close_python_dependencies(source, {3})
    assert 2 in result.reasons
    assert 5 not in result.reasons


def test_parameter_does_not_restore_same_named_global() -> None:
    result = close_python_dependencies(
        "value = 99\ndef target(value):\n    return value\n", {3}
    )
    assert 1 not in result.reasons


def test_branch_condition_dependencies_are_preserved() -> None:
    result = close_python_dependencies(
        "ENABLED = True\ndef target():\n    if ENABLED:\n        return 7\n", {4}
    )
    assert 1 in result.reasons


@pytest.mark.parametrize(
    "header",
    [
        "def target(\n    value: int,\n) -> int:\n    return value\n",
        "def target(value):\n    if (\n"
        "        value > 0\n    ):\n        return value\n",
    ],
)
def test_multiline_headers_are_complete(header: str) -> None:
    evidence = len(header.splitlines())
    result = close_python_dependencies(header, {evidence})
    assert set(range(1, evidence)) <= set(result.reasons)


def test_long_statements_are_not_cut_at_arbitrary_line_limit() -> None:
    source = "VALUES = [\n" + "    1,\n" * 30 + "]\n"
    result = close_python_dependencies(source, {15})
    assert set(range(1, 33)) == set(result.reasons) | {15}


def test_invalid_source_never_calls_model(tmp_path: Path) -> None:
    class NeverScorer:
        backend_id = "must-not-run"

        def score(self, request: PruneRequest):
            pytest.fail("Invalid source must bypass expensive inference")

    result = ContextPruner(NeverScorer(), ReceiptStore(tmp_path)).prune(
        PruneRequest(task="Inspect", content="def invalid(:", minimum_tokens=0)
    )
    assert result.bypass_reason == "source_parse_error"


def test_target_tokenizer_can_reject_false_estimated_savings(tmp_path: Path) -> None:
    from contextlens.pruning import SemanticScores

    class Scorer:
        backend_id = "fixture"

        def score(self, request: PruneRequest) -> SemanticScores:
            return SemanticScores(self.backend_id, {1: 1.0})

    source = "answer = 7\n" + "unused = 1\n" * 100
    result = ContextPruner(
        Scorer(),
        ReceiptStore(tmp_path),
        token_counter=lambda text: 200 if "omitted original" in text else 100,
    ).prune(PruneRequest(task="Inspect", content=source, minimum_tokens=0))
    assert result.bypass_reason == "no_net_reduction"
    assert result.text == source
