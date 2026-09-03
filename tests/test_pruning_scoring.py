from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from contextlens.pruning import HttpSemanticScorer, PruneRequest, SemanticScores


class _Response:
    def __init__(self, value: object) -> None:
        self._body = json.dumps(value).encode()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_semantic_scores_validate_line_coordinates() -> None:
    scores = SemanticScores("fixture", {1: 0.2, 4: 0.9}, document_score=0.8)
    assert scores.line_scores == {1: 0.2, 4: 0.9}

    with pytest.raises(ValueError, match="line numbers"):
        SemanticScores("fixture", {0: 0.5})
    with pytest.raises(ValueError, match="line scores"):
        SemanticScores("fixture", {1: 2.0})


def test_http_scorer_uses_compatible_request_and_response() -> None:
    scorer = HttpSemanticScorer("http://127.0.0.1:8123/prune")
    request = PruneRequest(
        task="Fix timeout",
        focus="Where is the request timeout selected?",
        content="import http\n\ndef send():\n    return http.get('/')\n",
    )
    captured: dict[str, object] = {}

    def open_request(message: object, *, timeout: float) -> _Response:
        captured["message"] = message
        captured["timeout"] = timeout
        return _Response(
            {
                "score": 0.87,
                "kept_frags": [1, 3, 4],
                "model_input_token_cnt": 91,
            }
        )

    with patch("urllib.request.urlopen", side_effect=open_request):
        result = scorer.score(request)

    message = captured["message"]
    payload = json.loads(message.data)
    assert payload["query"].startswith("Where is the request timeout")
    assert payload["code"] == request.content
    assert result.line_scores == {1: 1.0, 3: 1.0, 4: 1.0}
    assert result.document_score == 0.87
    assert result.input_tokens == 91


def test_http_scorer_rejects_backend_failure() -> None:
    scorer = HttpSemanticScorer()
    request = PruneRequest(task="Find parser", content="def parse(): pass")
    failure = urllib.error.URLError("offline")

    with (
        patch("urllib.request.urlopen", side_effect=failure),
        pytest.raises(RuntimeError, match="unavailable"),
    ):
        scorer.score(request)


def test_http_scorer_rejects_invalid_payload() -> None:
    scorer = HttpSemanticScorer()
    request = PruneRequest(task="Find parser", content="def parse(): pass")
    response = io.BytesIO(b"not-json")
    response.__enter__ = lambda: response  # type: ignore[attr-defined]

    with (
        patch("urllib.request.urlopen", return_value=response),
        pytest.raises(RuntimeError, match="invalid JSON"),
    ):
        scorer.score(request)
