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
        "context_next",
        "context_observe",
        "context_working_set",
        "context_recall",
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
    annotations = {tool["name"]: tool["annotations"] for tool in tools}
    assert annotations["context_working_set"]["readOnlyHint"] is True
    assert annotations["context_observe"]["readOnlyHint"] is False
    assert annotations["context_recall"]["readOnlyHint"] is False
    assert annotations["context_next"]["readOnlyHint"] is False
    schemas = {tool["name"]: tool["inputSchema"] for tool in tools}
    action_items = schemas["context_next"]["properties"]["actions"]["items"]
    assert action_items["required"] == ["id", "kind", "description"]
    assert "ready_to_edit" in action_items["properties"]["kind"]["enum"]
    assert "test_output" in schemas["context_observe"]["properties"]["type"]["enum"]


def test_mcp_exposes_bounded_next_action(service):
    result = dispatch(
        service,
        message(
            "tools/call",
            name="context_next",
            arguments={
                "task": "fix refresh expiry",
                "focus": "locate the TTL",
                "observations": [],
                "actions": [
                    {
                        "id": "search",
                        "kind": "search_repository",
                        "description": "Search for TTL",
                        "tool": "context_select",
                        "arguments": {"focus": "TTL"},
                    },
                    {
                        "id": "edit",
                        "kind": "ready_to_edit",
                        "description": "Start the patch",
                    },
                ],
            },
        ),
    )["result"]

    assert not result["isError"]
    decision = __import__("json").loads(result["content"][0]["text"])
    assert decision["selected"] in {"search", "edit"}
    assert set(decision["probabilities"]) == {"search", "edit"}


def test_mcp_rejects_unbounded_next_action(service):
    result = dispatch(
        service,
        message(
            "tools/call",
            name="context_next",
            arguments={"task": "task", "observations": [], "actions": []},
        ),
    )["result"]
    assert result["isError"]


def test_mcp_treats_action_tools_as_advisory_and_normalizes_summaries(service):
    result = dispatch(
        service,
        message(
            "tools/call",
            name="context_next",
            arguments={
                "task": "finish verification",
                "observations": ["focused tests passed"],
                "actions": [
                    {
                        "id": "diff",
                        "kind": "inspect_diff",
                        "description": "Inspect the patch",
                        "tool": "context_select",
                    },
                    {
                        "id": "stop",
                        "kind": "stop",
                        "description": "Stop after verification",
                        "tool": "context_select",
                    },
                ],
            },
        ),
    )["result"]
    assert not result["isError"]


def test_mcp_observations_can_be_listed_and_recalled(service):
    observed = dispatch(
        service,
        message(
            "tools/call",
            name="context_observe",
            arguments={
                "type": "test_output",
                "summary": "refresh test failed",
                "content": "expected 3600, got 900",
                "source": "pytest",
            },
        ),
    )["result"]
    handle = __import__("json").loads(observed["content"][0]["text"])["handle"]
    listing = dispatch(
        service, message("tools/call", name="context_working_set", arguments={})
    )["result"]
    assert handle in listing["content"][0]["text"]
    assert "expected 3600" not in listing["content"][0]["text"]
    recalled = dispatch(
        service,
        message("tools/call", name="context_recall", arguments={"handle": handle}),
    )["result"]
    assert "expected 3600, got 900" in recalled["content"][0]["text"]


def test_next_reports_working_set_and_capability_decisions(service):
    dispatch(
        service,
        message(
            "tools/call",
            name="context_observe",
            arguments={
                "type": "search_result",
                "summary": "TTL is in config.py",
                "content": "config.py:12",
            },
        ),
    )
    result = dispatch(
        service,
        message(
            "tools/call",
            name="context_next",
            arguments={
                "task": "fix expiry",
                "actions": [
                    {
                        "id": "read",
                        "kind": "read_source",
                        "description": "Read config",
                        "tool": "context_read",
                    },
                    {
                        "id": "edit",
                        "kind": "ready_to_edit",
                        "description": "Edit config",
                    },
                ],
            },
        ),
    )["result"]
    decision = __import__("json").loads(result["content"][0]["text"])
    assert decision["retention"]["kept"]
    assert set(decision["capability_probabilities"]) == {"read", "edit"}
    telemetry = (service.state / "controller_calls.jsonl").read_text()
    row = __import__("json").loads(telemetry)
    assert row["selected"] in {"read", "edit"}
    assert row["input_tokens"] == 300
    assert row["output_tokens"] == 6
    assert "config.py:12" not in telemetry


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
