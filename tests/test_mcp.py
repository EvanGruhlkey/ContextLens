from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from contextlens.filtering import PruneConfig
from contextlens.jev import Evaluation
from contextlens.mcp import PROTOCOLS, TOOLS, Server, dispatch, serve_stdio
from contextlens.receipts import ReceiptStore


class FakeJudge:
    def __init__(self, probability: float = 0.0) -> None:
        self.probability = probability

    def evaluate(
        self, state: Mapping[str, Any], questions: Mapping[str, Any]
    ) -> Evaluation:
        return Evaluation(
            {name: self.probability for name in questions}, "fake", 10, 2, None, 1.0
        )


def server(tmp_path: Path, probability: float = 0.0) -> Server:
    return Server(
        ReceiptStore(tmp_path),
        task="fix the parser",
        judge=FakeJudge(probability),
        config=PruneConfig(minimum_tokens=100),
    )


def request(method: str, **params: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def noise(lines: int = 900) -> str:
    return "\n".join(f"progress step {index} downloading" for index in range(lines))


def test_only_two_tools_are_exposed() -> None:
    assert [tool["name"] for tool in TOOLS] == ["context_prune", "context_recover"]


def test_initialize_echoes_a_supported_protocol(tmp_path: Path) -> None:
    response = dispatch(
        server(tmp_path), request("initialize", protocolVersion="2025-06-18")
    )
    assert response is not None
    assert response["result"]["protocolVersion"] == "2025-06-18"
    assert "ContextLens" in response["result"]["instructions"]
    unsupported = dispatch(
        server(tmp_path), request("initialize", protocolVersion="1999-01-01")
    )
    assert unsupported is not None
    assert unsupported["result"]["protocolVersion"] == PROTOCOLS[-1]


def test_ping_and_tools_list(tmp_path: Path) -> None:
    active = server(tmp_path)
    assert dispatch(active, request("ping")) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {},
    }
    listed = dispatch(active, request("tools/list"))
    assert listed is not None
    assert len(listed["result"]["tools"]) == 2


def test_notifications_get_no_response(tmp_path: Path) -> None:
    assert dispatch(server(tmp_path), {"jsonrpc": "2.0", "method": "ping"}) is None


def test_malformed_envelope_and_unknown_method(tmp_path: Path) -> None:
    active = server(tmp_path)
    invalid = dispatch(active, {"jsonrpc": "1.0"})
    assert invalid is not None and invalid["error"]["code"] == -32600
    unknown = dispatch(active, request("tools/unknown"))
    assert unknown is not None and unknown["error"]["code"] == -32601
    bad_params = dispatch(
        active, {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []}
    )
    assert bad_params is not None and bad_params["error"]["code"] == -32602


def test_prune_returns_kept_content_with_a_recovery_note(tmp_path: Path) -> None:
    response = dispatch(
        server(tmp_path),
        request("tools/call", name="context_prune", arguments={"output": noise()}),
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    assert "contextlens omitted" in text
    assert "context_recover cl_" in text
    assert len(text) < len(noise())


def test_prune_returns_small_output_unchanged(tmp_path: Path) -> None:
    response = dispatch(
        server(tmp_path),
        request("tools/call", name="context_prune", arguments={"output": "tiny"}),
    )
    assert response is not None
    assert response["result"]["content"][0]["text"] == "tiny"


def test_recover_returns_the_exact_original(tmp_path: Path) -> None:
    active = server(tmp_path)
    pruned = dispatch(
        active,
        request("tools/call", name="context_prune", arguments={"output": noise()}),
    )
    assert pruned is not None
    receipt = pruned["result"]["content"][0]["text"].rsplit("context_recover ", 1)[1]
    response = dispatch(
        active,
        request(
            "tools/call",
            name="context_recover",
            arguments={"receipt_id": receipt.rstrip("]")},
        ),
    )
    assert response is not None
    assert response["result"]["content"][0]["text"] == noise()


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"output": 3},
        {"output": "x", "unknown": "y"},
        {"output": "x", "path": 7},
    ],
)
def test_invalid_prune_arguments_are_rejected(
    tmp_path: Path, arguments: dict[str, Any]
) -> None:
    response = dispatch(
        server(tmp_path),
        request("tools/call", name="context_prune", arguments=arguments),
    )
    assert response is not None
    assert response["result"]["isError"] is True


def test_invalid_recover_arguments_are_rejected(tmp_path: Path) -> None:
    active = server(tmp_path)
    for arguments in (
        {"receipt_id": "nope"},
        {"receipt_id": "cl_" + "0" * 24, "start_line": 0},
        {"receipt_id": "cl_" + "0" * 24},
    ):
        response = dispatch(
            active, request("tools/call", name="context_recover", arguments=arguments)
        )
        assert response is not None
        assert response["result"]["isError"] is True


def test_unknown_tool_is_rejected(tmp_path: Path) -> None:
    response = dispatch(
        server(tmp_path), request("tools/call", name="context_next", arguments={})
    )
    assert response is not None
    assert response["result"]["isError"] is True


def test_serve_stdio_answers_line_by_line(tmp_path: Path) -> None:
    lines = [
        json.dumps(request("initialize")),
        json.dumps(request("tools/list")),
        "{not json",
    ]
    output = io.StringIO()
    serve_stdio(
        ReceiptStore(tmp_path),
        task="fix",
        input_stream=io.StringIO("\n".join(lines) + "\n"),
        output_stream=output,
    )
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(responses) == 3
    assert responses[0]["result"]["serverInfo"]["name"] == "contextlens"
    assert responses[2]["error"]["code"] == -32700


def test_serve_stdio_rejects_oversized_requests(tmp_path: Path) -> None:
    output = io.StringIO()
    serve_stdio(
        ReceiptStore(tmp_path),
        input_stream=io.StringIO("x" * (1024 * 1024 + 10) + "\n"),
        output_stream=output,
    )
    first = json.loads(output.getvalue().splitlines()[0])
    assert first["error"]["code"] == -32700
    assert first["error"]["message"] == "request exceeds 1 MiB"
