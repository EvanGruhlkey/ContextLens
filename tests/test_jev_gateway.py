import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from contextlens import jev_gateway


def test_gateway_uses_vercel_evaluation_contract(monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                (
                    self.path,
                    self.headers,
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "model": "typesafe-ai/jev",
                        "answers": {"c0": {"type": "boolean", "probability": 0.9}},
                        "usage": {"inputTokens": 123, "outputTokens": 4},
                        "providerMetadata": {"gateway": {"cost": "0.0001"}},
                    }
                ).encode()
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-key")
    monkeypatch.setattr(
        jev_gateway, "ENDPOINT", f"http://127.0.0.1:{server.server_port}/v1/evaluate"
    )
    try:
        result = jev_gateway.JevGateway().evaluate(
            {"task": "fix timeout"},
            {"c0": {"type": "boolean", "instructions": "Useful?"}},
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    path, headers, body = requests[0]
    assert path == "/v1/evaluate"
    assert headers["Authorization"] == "Bearer test-key"
    assert body["model"] == "typesafe-ai/jev"
    assert body["state"] == {"task": "fix timeout"}
    assert body["providerOptions"]["gateway"]["only"] == ["typesafe-ai"]
    assert body["providerOptions"]["gateway"]["zeroDataRetention"] is True
    assert result.probabilities == {"c0": 0.9}
    assert result.input_tokens == 123
    assert result.cost == "0.0001"


@pytest.mark.parametrize(
    "answers",
    [
        {},
        {"c0": {"type": "boolean", "probability": True}},
        {"c0": {"type": "boolean", "probability": float("nan")}},
        {"c0": {"type": "boolean", "probability": 1.1}},
        {"other": {"type": "boolean", "probability": 0.9}},
        {"c0": {"type": "choice", "probability": 0.9}},
    ],
)
def test_gateway_rejects_missing_or_invalid_decisions(answers):
    with pytest.raises(jev_gateway.GatewayError, match="invalid"):
        jev_gateway.parse_evaluation({"answers": answers}, {"c0"}, 1.0)


def test_gateway_requires_credentials_without_revealing_secrets(monkeypatch):
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    with pytest.raises(jev_gateway.GatewayError, match="AI_GATEWAY_API_KEY"):
        jev_gateway.JevGateway().evaluate({}, {"c0": {}})


def test_unknown_usage_is_not_zero():
    result = jev_gateway.parse_evaluation(
        {"answers": {"c0": {"type": "boolean", "probability": 0.3}}}, {"c0"}, 1.0
    )
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.cost is None
