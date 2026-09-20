import json
import subprocess

from contextlens.context_mcp import dispatch
from contextlens.filter_context import FilterContext
from contextlens.jev_gateway import Evaluation, JevGateway
from contextlens.pruning_cli import build_parser


def message(method, **params):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def test_default_cli_profile_is_filter():
    args = build_parser().parse_args(["mcp"])
    assert args.profile == "filter"


def test_filter_mcp_exposes_a_small_surface(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "auth.py").write_text("TIMEOUT = 1\n\ndef refresh():\n    return TIMEOUT\n")
    monkeypatch.setattr(
        JevGateway,
        "evaluate",
        lambda self, state, questions: Evaluation(
            {key: 0.9 for key in questions}, "typesafe-ai/jev", 8, 1, None, 1
        ),
    )
    session = FilterContext(root, tmp_path / "state")
    tools = dispatch(session, message("tools/list"))["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "context_filter",
        "context_read",
        "context_recover",
        "context_pin",
        "context_list",
    ]
    instructions = dispatch(session, message("initialize"))["result"]["instructions"]
    assert "does not choose the next action" in instructions
    result = dispatch(
        session,
        message(
            "tools/call",
            name="context_filter",
            arguments={
                "task": "fix refresh",
                "content": "auth.py:3:def refresh():\nnotes.md:1:unrelated",
                "tool": "rg",
                "kind": "search",
            },
        ),
    )["result"]
    assert not result["isError"]
    listing = json.loads(
        dispatch(session, message("tools/call", name="context_list", arguments={}))[
            "result"
        ]["content"][0]["text"]
    )
    assert listing["active"] or listing["pinned"]
    pinned = dispatch(
        session,
        message(
            "tools/call",
            name="context_pin",
            arguments={
                "summary": "keep the timeout requirement",
                "type": "user_constraint",
            },
        ),
    )["result"]
    assert not pinned["isError"]
