import json
import subprocess

import pytest

from contextlens.context_adapter import Answer, ContextAdapter, ToolCall
from contextlens.context_mcp import dispatch
from contextlens.context_tools import RepositoryContext
from contextlens.pruning import ReceiptStore


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "auth.py").write_bytes(
        b"TIMEOUT = 30\r\n\r\ndef refresh_token():\r\n    return TIMEOUT\r\n"
        b"\r\ndef irrelevant():\r\n    return 'UNRELATED_SECRET_SENTINEL'\r\n"
    )
    return RepositoryContext(root, tmp_path / "state", encoding="o200k_base")


def test_discovery_is_locations_only_and_read_keeps_support(repository):
    locations = repository.call("find", {"query": "refresh_token", "limit": 1})
    assert "return TIMEOUT" not in locations
    handle = locations.splitlines()[1].split()[0]
    response = repository.call("read", {"handle": handle})
    assert "TIMEOUT = 30\r\n" in response
    assert "def refresh_token():\r\n" in response
    assert "UNRELATED_SECRET_SENTINEL" not in response
    assert repository.count(response) <= 3000


def test_stale_read_and_exact_snapshot_survive_restart(repository):
    locations = repository.call("find", {"query": "refresh_token", "limit": 1})
    handle = locations.splitlines()[1].split()[0]
    (repository.root / "auth.py").write_text("changed\n", encoding="utf-8")
    restarted = RepositoryContext(repository.root, repository.state)
    assert restarted.call("read", {"handle": handle}).startswith("Stale evidence")
    historical = restarted.call("expand", {"handle": handle})
    assert historical.startswith("HISTORICAL SNAPSHOT")
    assert "return TIMEOUT\r\n" in historical


def test_budget_refuses_incomplete_evidence_instead_of_truncating(repository):
    source = "def large():\n" + "    value = 1000\n" * 500
    (repository.root / "large.py").write_text(source)
    response = repository.call("read", {"path": "large.py", "budget": 128})
    assert "exceed the response budget" in response
    assert "def large" not in response
    assert repository.count(response) <= 128


def test_direct_read_confines_paths_and_preserves_empty_files(repository):
    with pytest.raises(ValueError, match="outside the repository"):
        repository.call("read", {"path": "../outside.py"})
    (repository.root / "empty.py").write_bytes(b"")
    assert "Exact current source" in repository.call("read", {"path": "empty.py"})
    with pytest.raises(ValueError, match="outside the source"):
        repository.call("read", {"path": "auth.py", "start_line": 1, "end_line": 900})


def test_handle_tampering_and_cross_repository_use_are_rejected(repository, tmp_path):
    locations = repository.call("find", {"query": "refresh_token", "limit": 1})
    handle = locations.splitlines()[1].split()[0]
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = RepositoryContext(other_root, repository.state)
    with pytest.raises(ValueError, match="another repository"):
        other.call("read", {"handle": handle})
    metadata = repository.handles / (handle + ".json")
    metadata.write_text(metadata.read_text() + " ")
    with pytest.raises(RuntimeError, match="integrity"):
        repository.call("read", {"handle": handle})


def test_mcp_returns_plain_source_and_rejects_invalid_arguments(repository):
    result = dispatch(
        repository,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "context_read",
                "arguments": {"path": "auth.py", "start_line": 3, "end_line": 4},
            },
        },
    )
    text = result["result"]["content"][0]["text"]
    assert "def refresh_token():\r\n" in text
    assert "\\r\\n" not in text
    assert not result["result"]["isError"]
    assert json.loads(json.dumps(result))["result"]["content"][0]["text"] == text
    invalid = dispatch(
        repository,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "context_read",
                "arguments": {"path": "auth.py", "budget": True},
            },
        },
    )
    assert invalid["result"]["isError"]


def test_owned_solver_receives_only_requested_repository_evidence(repository, tmp_path):
    adapter = ContextAdapter(repository, ReceiptStore(tmp_path / "audit"))
    calls = 0

    def solver(messages):
        nonlocal calls
        assert all(
            "UNRELATED_SECRET_SENTINEL" not in message.content for message in messages
        )
        calls += 1
        if calls == 1:
            return ToolCall("find", {"query": "refresh_token", "limit": 1})
        if calls == 2:
            handle = messages[-1].content.splitlines()[1].split()[0]
            return ToolCall("read", {"handle": handle})
        assert "return TIMEOUT" in messages[-1].content
        return Answer("ready to fix")

    assert adapter.run(solver, "Fix refresh_token") == "ready to fix"


def test_coverage_requires_acknowledgment_and_resets(repository):
    args = {"path": "auth.py", "start_line": 3, "end_line": 4}
    repository.begin_context()
    response = repository.call("read", args)
    assert "return TIMEOUT" in repository.call("read", args)
    repository.acknowledge(response)
    assert "already visible" in repository.call("read", args)
    assert "return TIMEOUT" in repository.call("read", args | {"reread": True})
    repository.begin_context()
    assert "return TIMEOUT" in repository.call("read", args)


def test_native_reads_repeat_and_partial_reads_disclose_missing_support(repository):
    locations = repository.call("find", {"query": "refresh_token", "limit": 1})
    handle = locations.splitlines()[1].split()[0]
    args = {"handle": handle}
    assert repository.call("read", args) == repository.call("read", args)
    partial = repository.call("read", args | {"start_line": 3, "end_line": 4})
    assert "support omitted" in partial
    assert "TIMEOUT = 30" not in partial


def test_overlap_only_suppresses_acknowledged_current_version(repository):
    repository.begin_context()
    response = repository.call(
        "read", {"path": "auth.py", "start_line": 1, "end_line": 3}
    )
    repository.acknowledge(response)
    remainder = repository.call(
        "read", {"path": "auth.py", "start_line": 3, "end_line": 4}
    )
    assert "def refresh_token" not in remainder
    assert "return TIMEOUT" in remainder
    (repository.root / "auth.py").write_text("TIMEOUT = 60\n")
    assert "TIMEOUT = 60" in repository.call("read", {"path": "auth.py"})
