import subprocess

import pytest

from contextlens.context_adapter import Answer, ContextAdapter, ToolCall
from contextlens.context_mcp import dispatch
from contextlens.jev_context import JevRepositoryContext
from contextlens.jev_gateway import Evaluation, JevGateway
from contextlens.pruning import ReceiptStore
from contextlens.pruning_cli import build_parser, main


@pytest.fixture
def service(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "sample.py").write_text("def refresh():\n    return 42\n")
    monkeypatch.setattr(
        JevGateway,
        "evaluate",
        lambda self, state, questions: Evaluation(
            {key: 0.9 for key in questions}, "typesafe-ai/jev", 100, 2, None, 1
        ),
    )
    return JevRepositoryContext(root, tmp_path / "state")


def message(method, **params):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def test_default_cli_profile_uses_jev():
    args = build_parser().parse_args(["mcp"])
    assert args.profile == "jev"
    assert args.encoding == "o200k_base"


def test_select_cli_returns_source(service, capsys):
    assert (
        main(
            [
                "select",
                "--task",
                "refresh",
                "--root",
                str(service.root),
                "--state",
                str(service.state),
            ]
        )
        == 0
    )
    assert "return 42" in capsys.readouterr().out


def test_mcp_exposes_selection_and_exact_recovery(service):
    tools = dispatch(service, message("tools/list"))["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "context_select",
        "context_read",
        "context_expand",
    ]
    result = dispatch(
        service,
        message("tools/call", name="context_select", arguments={"task": "refresh"}),
    )["result"]
    assert not result["isError"]
    assert "return 42" in result["content"][0]["text"]
    instructions = dispatch(service, message("initialize"))["result"]["instructions"]
    assert "context_select" in instructions
    assert "Vercel" in tools[0]["description"]


def test_mcp_rejects_invalid_select_arguments(service):
    for args in [
        {},
        {"task": 4},
        {"task": "refresh", "budget": False},
        {"task": "refresh", "limit": 21},
        {"task": "refresh", "other": "x"},
    ]:
        result = dispatch(
            service, message("tools/call", name="context_select", arguments=args)
        )["result"]
        assert result["isError"]


def test_adapter_acknowledges_selected_evidence(service, tmp_path):
    adapter = ContextAdapter(service, ReceiptStore(tmp_path / "receipts"))
    calls = 0

    def solver(history):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ToolCall("select", {"task": "refresh"})
        assert "return 42" in history[-1].content
        assert service.visible
        return Answer("done")

    assert adapter.run(solver, "refresh") == "done"


def test_direct_mcp_reads_need_no_gateway_key(service, monkeypatch):
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    result = dispatch(
        service,
        message("tools/call", name="context_read", arguments={"path": "sample.py"}),
    )["result"]
    assert not result["isError"]
    assert "return 42" in result["content"][0]["text"]
