from __future__ import annotations

from pathlib import Path

import pytest

from contextlens.pruning import PruneRequest, ReceiptStore


def test_receipt_store_recovers_full_content_and_ranges(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path / "receipts")
    request = PruneRequest(
        task="Fix timeout",
        content="one\ntwo\nthree\nfour\n",
        tool="read_file",
        language="python",
    )

    receipt = store.save(request)

    assert receipt.receipt_id.startswith("cl_")
    assert store.read(receipt.receipt_id) == request.content
    assert store.read(receipt.receipt_id, start_line=2, end_line=3) == "two\nthree\n"
    assert store.metadata(receipt.receipt_id).content_hash == request.content_hash


def test_receipt_save_is_idempotent(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path)
    request = PruneRequest(task="Inspect parser", content="def parse():\n    pass\n")

    first = store.save(request)
    second = store.save(request)

    assert first.receipt_id == second.receipt_id
    assert len(list(tmp_path.glob("*.txt"))) == 1


def test_receipt_store_rejects_traversal_and_partial_ranges(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path)

    with pytest.raises(ValueError, match="identifier"):
        store.read("../secret")

    request = PruneRequest(task="Inspect parser", content="one\ntwo")
    receipt = store.save(request)
    with pytest.raises(ValueError, match="supplied together"):
        store.read(receipt.receipt_id, start_line=1)
    with pytest.raises(ValueError, match="line range"):
        store.read(receipt.receipt_id, start_line=2, end_line=1)
