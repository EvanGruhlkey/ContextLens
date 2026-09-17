from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from contextlens.evidence_mcp import dispatch, serve_stdio
from contextlens.evidence_session import EvidenceSession


def session(tmp_path: Path) -> EvidenceSession:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "auth.py").write_bytes(b"def refresh():\r\n    return 30\r\n")
    return EvidenceSession(tmp_path, tmp_path / ".contextlens")


def test_read_expand_and_verify(tmp_path: Path) -> None:
    tools = session(tmp_path)
    result = tools.call("read", {"path": "auth.py"})
    assert result["content"] == "def refresh():\r\n    return 30\r\n"
    assert tools.call(
        "verify", {"path": "auth.py", "expected_hash": result["content_hash"]}
    )["current_source_verified"]
    (tmp_path / "auth.py").write_bytes(b"changed = True\n")
    with pytest.raises(ValueError, match="changed"):
        tools.call(
            "verify", {"path": "auth.py", "expected_hash": result["content_hash"]}
        )
    assert (
        tools.call("expand", {"receipt_id": result["receipt_id"]})["content"]
        == result["content"]
    )
    assert len(tools.calls) == 4
    assert tools.calls[2]["error"]


def test_external_memory_offloads_and_recovers(tmp_path: Path) -> None:
    tools = session(tmp_path)
    tools.call("remember", {"content": "retain timeout units", "pinned": True})
    remembered = tools.call("remember", {"content": "large log\n" * 200})
    view = tools.call("view", {"budget": 100})
    assert view["working_memory"][0]["pinned"]
    assert view["deferred_count"] == 1
    assert not view["hosted_conversation_modified"]
    resumed = EvidenceSession(tmp_path, tmp_path / ".contextlens")
    assert len(resumed.memory) == 2
    assert resumed.call("expand", remembered)["content"] == "large log\n" * 200


def test_protocol_handshake_errors_and_stdio(tmp_path: Path) -> None:
    tools = session(tmp_path)
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "evidence_read", "arguments": {"path": "auth.py"}},
        },
    ]
    output = io.StringIO()
    serve_stdio(
        tools, io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n"), output
    )
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(rows) == 2
    assert rows[0]["result"]["protocolVersion"] == "2025-06-18"
    assert not rows[1]["result"]["isError"]
    for args in (
        {"path": "../secret"},
        {"path": "auth.py", "budget": True},
        {"path": "auth.py", "typo": 1},
    ):
        result = dispatch(
            tools,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "evidence_read", "arguments": args},
            },
        )
        assert result["result"]["isError"]


def test_tampered_receipt_is_rejected(tmp_path: Path) -> None:
    tools = session(tmp_path)
    result = tools.call("read", {"path": "auth.py"})
    receipt = tools.receipts.root / (result["receipt_id"] + ".txt")
    receipt.write_text("tampered\n")
    with pytest.raises(RuntimeError, match="integrity"):
        tools.call("expand", {"receipt_id": result["receipt_id"]})


def test_long_read_signals_expansion_and_observe_fails_open(tmp_path: Path) -> None:
    tools = session(tmp_path)
    result = tools.call("read", {"path": "auth.py", "budget": 1})
    assert result["content"] == ""
    assert "exceeds_budget" in result["status"]
    observed = tools.call("observe", {"task": "refresh", "content": "source"})
    assert observed["content"] == "source"


def test_deduplication_requires_same_current_content_and_expands(
    tmp_path: Path,
) -> None:
    tools = session(tmp_path)
    first = tools.call("read", {"path": "auth.py", "deduplicate": True})
    repeated = tools.call("read", {"path": "auth.py", "deduplicate": True})
    assert repeated["content"] == ""
    assert "already_returned" in repeated["status"]
    assert (
        tools.call("expand", {"receipt_id": first["receipt_id"]})["content"]
        == first["content"]
    )
    (tmp_path / "auth.py").write_bytes(b"changed = True\n")
    changed = tools.call("read", {"path": "auth.py", "deduplicate": True})
    assert changed["content"] == "changed = True\n"
