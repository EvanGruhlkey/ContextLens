from __future__ import annotations

from pathlib import Path

import pytest

from contextlens.receipts import ReceiptStore


def test_save_and_read_round_trips_exactly(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path)
    content = "line one\nline two\nline three\n"
    receipt = store.save(content, tool="shell", category="build")
    assert receipt.receipt_id.startswith("cl_")
    assert receipt.line_count == 3
    assert receipt.char_count == len(content)
    assert store.read(receipt.receipt_id) == content
    assert store.recoveries == 1
    assert store.recovered_tokens > 0


def test_metadata_survives_a_reopened_store(tmp_path: Path) -> None:
    receipt = ReceiptStore(tmp_path).save("content", tool="grep")
    metadata = ReceiptStore(tmp_path).metadata(receipt.receipt_id)
    assert metadata == receipt


def test_saving_identical_content_reuses_one_receipt(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path)
    first = store.save("same")
    second = store.save("same")
    assert first.receipt_id == second.receipt_id
    assert len(list(tmp_path.glob("*.txt"))) == 1


def test_line_range_recovery(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path)
    receipt = store.save("a\nb\nc\nd\n")
    assert store.read(receipt.receipt_id, start_line=2, end_line=3) == "b\nc\n"


def test_line_range_requires_both_bounds(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path)
    receipt = store.save("a\nb\n")
    with pytest.raises(ValueError):
        store.read(receipt.receipt_id, start_line=1)
    with pytest.raises(ValueError):
        store.read(receipt.receipt_id, start_line=0, end_line=1)
    with pytest.raises(ValueError):
        store.read(receipt.receipt_id, start_line=3, end_line=2)


def test_unknown_and_malformed_handles(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path)
    with pytest.raises(ValueError):
        store.read("not-a-receipt")
    with pytest.raises(KeyError):
        store.read("cl_" + "0" * 24)
    with pytest.raises(KeyError):
        store.metadata("cl_" + "0" * 24)


def test_tampered_content_fails_the_integrity_check(tmp_path: Path) -> None:
    store = ReceiptStore(tmp_path)
    receipt = store.save("original")
    (tmp_path / f"{receipt.receipt_id}.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeError):
        store.read(receipt.receipt_id)
