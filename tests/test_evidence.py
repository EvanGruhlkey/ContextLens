from __future__ import annotations

import subprocess
from pathlib import Path

from contextlens.evidence import retrieve_evidence
from contextlens.pruning import ReceiptStore


def repository(tmp_path: Path, source: str) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "auth.py").write_bytes(source.encode())
    return tmp_path


def test_retrieval_preserves_helper_body_and_decorators(tmp_path: Path) -> None:
    source = (
        "TIMEOUT = 30\r\n"
        "def helper():\r\n    return TIMEOUT * 1000\r\n"
        "@staticmethod\r\n"
        "def refresh_token():\r\n    return helper()\r\n"
    )
    root = repository(tmp_path, source)
    store = ReceiptStore(root / ".receipts")
    result = retrieve_evidence(root, "refresh token", store)
    text = "".join(span["text"] for span in result["spans"])
    assert "return TIMEOUT * 1000\r\n" in text
    assert "@staticmethod\r\n" in text
    assert "TIMEOUT = 30\r\n" in text
    assert result["unresolved_dependencies"] == []
    (root / "auth.py").write_text("changed = True\n")
    assert store.read(result["spans"][0]["receipt_id"]) == source


def test_budget_exposes_missing_dependency(tmp_path: Path) -> None:
    source = (
        "def helper():\n" + "    unused = 1\n" * 100 + "    return 30\n"
        "def refresh():\n    return helper()\n"
    )
    result = retrieve_evidence(
        repository(tmp_path, source),
        "refresh",
        ReceiptStore(tmp_path / ".r"),
        budget=30,
    )
    assert result["source_tokens"] <= 30
    assert result["unresolved_dependencies"][0]["symbols"] == ["helper"]
    assert all("pass" not in span["text"] for span in result["spans"])


def test_ignored_files_and_invalid_python(tmp_path: Path) -> None:
    root = repository(tmp_path, "def invalid(:\n")
    (root / ".gitignore").write_text("secret.py\n")
    (root / "secret.py").write_text("refresh = 1\n")
    result = retrieve_evidence(root, "refresh", ReceiptStore(root / ".r"))
    assert result["spans"] == []
    assert result["skipped"] == [
        {"path": "auth.py", "reason": "unreadable_or_invalid_python"}
    ]


def test_unrelated_task_returns_no_evidence(tmp_path: Path) -> None:
    root = repository(tmp_path, "answer = 7\n")
    result = retrieve_evidence(root, "frobnicate", ReceiptStore(root / ".r"))
    assert result["spans"] == []
