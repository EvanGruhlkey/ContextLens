"""Small local HTTP surface for observation pruning and recovery."""

from __future__ import annotations

import json
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from contextlens.pruning.model import ObservationKind, PruneRequest
from contextlens.pruning.pipeline import ContextPruner
from contextlens.pruning.receipts import ReceiptStore
from contextlens.pruning.scoring import SemanticScorer

MAX_REQUEST_BYTES = 16 * 1024 * 1024


class PruningService:
    """Validate transport payloads before invoking the core pipeline."""

    def __init__(self, scorer: SemanticScorer, receipts: ReceiptStore) -> None:
        self.pruner = ContextPruner(scorer, receipts)
        self.receipts = receipts

    def prune(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        task = _required_string(payload, "task")
        content = _required_string(payload, "content", allow_empty=True)
        request = PruneRequest(
            task=task,
            content=content,
            focus=_optional_string(payload, "focus"),
            tool=_optional_string(payload, "tool"),
            arguments=_arguments(payload.get("arguments")),
            kind=ObservationKind(payload.get("kind", "code")),
            language=_optional_string(payload, "language", default="python"),
            threshold=_number(payload, "threshold", 0.5),
            minimum_tokens=_integer(payload, "minimum_tokens", 256),
            dependency_hops=_integer(payload, "dependency_hops", 2),
            context_radius=_integer(payload, "context_radius", 1),
        )
        return self.pruner.prune(request).to_dict()

    def recover(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        receipt_id = _required_string(payload, "receipt_id")
        start = _optional_integer(payload, "start_line")
        end = _optional_integer(payload, "end_line")
        return {
            "receipt_id": receipt_id,
            "content": self.receipts.read(
                receipt_id,
                start_line=start,
                end_line=end,
            ),
        }


def serve(
    *,
    host: str,
    port: int,
    receipts: Path,
    scorer: SemanticScorer,
) -> None:
    """Run the local service until interrupted."""

    service = PruningService(scorer, ReceiptStore(receipts))
    handler = _handler_for(service)
    with ThreadingHTTPServer((host, port), handler) as server:
        server.serve_forever()


def _handler_for(service: PruningService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ContextLens/0.1"

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._respond(HTTPStatus.OK, {"status": "ok"})
                return
            self._respond(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            operations = {
                "/v1/prune": service.prune,
                "/v1/recover": service.recover,
            }
            operation = operations.get(self.path)
            if operation is None:
                self._respond(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                payload = self._read_payload()
                self._respond(HTTPStatus.OK, operation(payload))
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
                self._respond(HTTPStatus.BAD_REQUEST, {"error": str(error)})

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_payload(self) -> Mapping[str, Any]:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("Content-Length is required")
            length = int(raw_length)
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("request body is too large")
            try:
                payload = json.loads(self.rfile.read(length))
            except json.JSONDecodeError as error:
                raise ValueError("request body must be valid JSON") from error
            if not isinstance(payload, dict):
                raise TypeError("request body must be an object")
            return cast(dict[str, Any], payload)

        def _respond(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _required_string(
    payload: Mapping[str, Any], key: str, *, allow_empty: bool = False
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(
    payload: Mapping[str, Any], key: str, *, default: str | None = None
) -> str | None:
    value = payload.get(key, default)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{key} must be a string or null")
    return value


def _arguments(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("arguments must be an object")
    return cast(dict[str, Any], value)


def _number(payload: Mapping[str, Any], key: str, default: float) -> float:
    value = payload.get(key, default)
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(f"{key} must be a number")
    return float(value)


def _integer(payload: Mapping[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key} must be an integer")
    return value


def _optional_integer(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise TypeError(f"{key} must be an integer or null")
    return value
