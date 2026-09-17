from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from contextlens.evidence import retrieve_evidence, verify_source
from contextlens.evidence_index import build_index
from contextlens.pruning import ReceiptStore


def repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name, source in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.encode())
    return tmp_path


def test_relative_alias_and_constant_chain(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            "pkg/auth.py": "from .config import timeout as delay\n"
            "def refresh():\n    return delay()\n",
            "pkg/config.py": "TIMEOUT = 30\n"
            "def timeout():\n    return TIMEOUT * 1000\n",
            "other/config.py": "TIMEOUT = 99\n",
        },
    )
    result = retrieve_evidence(
        root,
        "refresh",
        ReceiptStore(root / ".contextlens/receipts"),
        policy="dependency",
    )
    spans = result["spans"]
    assert any(
        s["path"] == "pkg/config.py" and "return TIMEOUT * 1000" in s["text"]
        for s in spans
    )
    assert not any(s["path"] == "other/config.py" for s in spans)


def test_module_attribute_import(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            "auth.py": "import settings as cfg\n"
            "def refresh():\n    return cfg.timeout()\n",
            "settings.py": "def timeout():\n    return 30\n",
        },
    )
    result = retrieve_evidence(
        root,
        "refresh",
        ReceiptStore(root / ".contextlens/receipts"),
        policy="dependency",
    )
    assert any(s["path"] == "settings.py" for s in result["spans"])


def test_cycle_and_content_cache_refresh(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            "a.py": "def first():\n    return second()\n"
            "def second():\n    return first()\n"
        },
    )
    cache = root / ".contextlens/index.sqlite"
    first = build_index(root, cache)
    second = build_index(root, cache)
    assert second.cache_hits == 1
    assert first.version == second.version
    (root / "a.py").write_text("def first():\n    return 99\n")
    third = build_index(root, cache)
    assert third.version != first.version
    assert third.cache_hits == 0
    result = retrieve_evidence(
        root,
        "first",
        ReceiptStore(root / ".contextlens/receipts"),
        index=first,
        policy="dependency",
    )
    assert len(result["spans"]) == 2


def test_ts_import_alias_preserves_export_body(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_typescript")
    root = repo(
        tmp_path,
        {
            "auth.ts": "import { timeout as delay } from './settings';\n"
            "export function refreshToken() { return delay(); }\n",
            "settings.ts": "export function timeout() { return 30; }\n",
        },
    )
    result = retrieve_evidence(
        root,
        "refresh token",
        ReceiptStore(root / ".contextlens/receipts"),
        policy="dependency",
    )
    assert any(
        s["path"] == "settings.ts" and "return 30" in s["text"] for s in result["spans"]
    )


def test_response_budget_and_stale_edit_guard(tmp_path: Path) -> None:
    root = repo(tmp_path, {"auth.py": "def refresh():\n    return 30\n"})
    store = ReceiptStore(root / ".contextlens/receipts")
    result = retrieve_evidence(
        root, "refresh", store, response_budget=1000, policy="dependency"
    )
    assert result["response_tokens"] <= 1000
    assert result["response_tokens"] == (len(json.dumps(result).encode()) + 3) // 4
    span = result["spans"][0]
    verify_source(root, span["path"], span["content_hash"])
    (root / "auth.py").write_text("modified = 1\n")
    with pytest.raises(ValueError, match="source changed"):
        verify_source(root, span["path"], span["content_hash"])
    with pytest.raises(ValueError, match="outside"):
        verify_source(root, "../escape.py", hashlib.sha256(b"").hexdigest())


def test_external_import_is_explicit(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {"auth.py": "import external\ndef refresh():\n    return external.call()\n"},
    )
    result = retrieve_evidence(
        root,
        "refresh",
        ReceiptStore(root / ".contextlens/receipts"),
        policy="dependency",
    )
    assert any(
        u["reason"] == "external_or_unresolved_import"
        for u in result["unresolved_dependencies"]
    )


def test_response_budget_reports_removed_source(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {"auth.py": "def refresh():\n    return '" + "x" * 2000 + "'\n"},
    )
    store = ReceiptStore(root / ".contextlens/receipts")
    result = retrieve_evidence(
        root, "refresh", store, budget=3000, response_budget=500, policy="dependency"
    )
    assert result["spans"] == []
    assert result["response_budget_omission_count"] == 1
    assert result["omitted_count"] == 1
    assert result["source_tokens"] == 0
    assert result["status"] == "response_budget_limited_expand_required"
