import json
import subprocess

import pytest

from contextlens.jev_context import JevRepositoryContext
from contextlens.jev_gateway import Evaluation, GatewayError


class Judge:
    def __init__(self, probability=None, mutate=None):
        self.probability = probability or (lambda unit: 0.9)
        self.mutate = mutate
        self.requests = []

    def evaluate(self, state, questions):
        self.requests.append((state, questions))
        if self.mutate:
            self.mutate()
        return Evaluation(
            {key: self.probability(unit) for key, unit in state["candidates"].items()},
            "typesafe-ai/jev",
            500,
            10,
            "0.000021",
            15.0,
        )


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "auth.py").write_bytes(
        b"TIMEOUT = 30\r\n\r\ndef refresh_token():\r\n    return TIMEOUT\r\n"
        b'\r\ndef refresh_unrelated():\r\n    return "UNRELATED"\r\n'
    )
    return root


def context(repo, tmp_path, judge):
    return JevRepositoryContext(
        repo, tmp_path / "state", judge=judge, encoding="o200k_base"
    )


def test_jev_selects_exact_source_and_support_in_one_call(repo, tmp_path):
    judge = Judge(lambda unit: 0.1 if "UNRELATED" in unit["source"] else 0.9)
    service = context(repo, tmp_path, judge)
    response = service.call("select", {"task": "fix refresh_token timeout"})
    assert "def refresh_token():\r\n    return TIMEOUT\r\n" in response
    assert "TIMEOUT = 30\r\n" in response
    assert 'return "UNRELATED"' not in response
    assert len(judge.requests) == 1
    assert len(judge.requests[0][1]) == len(judge.requests[0][0]["candidates"])
    assert service.count(response) <= 3000
    audit = json.loads(
        (service.state / "selections.jsonl").read_text().splitlines()[-1]
    )
    assert audit["evaluation"]["input_tokens"] == 500
    assert audit["evaluation"]["cost"] == "0.000021"


def test_large_support_does_not_refuse_small_implementation(repo, tmp_path):
    (repo / "auth.py").write_text(
        "VALUES = [\n"
        + ",\n".join(str(i) for i in range(2000))
        + "\n]\n\ndef refresh_token():\n    return VALUES[0]\n"
    )
    service = context(repo, tmp_path, Judge())
    response = service.call("select", {"task": "refresh_token", "budget": 200})
    assert "def refresh_token():" in response
    assert "return VALUES[0]" in response
    assert "deferred" in response.lower()
    assert service.count(response) <= 200


def test_selection_can_abstain_and_deferred_source_is_recoverable(repo, tmp_path):
    service = context(repo, tmp_path, Judge(lambda unit: 0.1))
    response = service.call("select", {"task": "refresh_token"})
    assert "No source selected" in response
    assert "return TIMEOUT" not in response
    handle = next(word for word in response.split() if word.startswith("h_"))
    assert "Exact current source" in service.call("read", {"handle": handle})


def test_source_changed_during_inference_is_not_delivered(repo, tmp_path):
    judge = Judge(mutate=lambda: (repo / "auth.py").write_text("changed\n"))
    service = context(repo, tmp_path, judge)
    with pytest.raises(ValueError, match="changed"):
        service.call("select", {"task": "refresh_token"})


def test_gateway_failure_is_not_disguised_as_success(repo, tmp_path):
    class Broken:
        def evaluate(self, state, questions):
            raise GatewayError("Vercel unavailable")

    service = context(repo, tmp_path, Broken())
    with pytest.raises(GatewayError):
        service.call("select", {"task": "refresh_token"})


def test_selection_input_is_bounded_and_does_not_cut_source(repo, tmp_path):
    judge = Judge()
    service = context(repo, tmp_path, judge)
    service.call("select", {"task": "refresh_token"})
    state, questions = judge.requests[0]
    assert service.count(json.dumps({"state": state, "questions": questions})) <= 20000
    for unit in state["candidates"].values():
        lines = (repo / unit["path"]).read_bytes().decode().splitlines(keepends=True)
        assert unit["source"] == "".join(
            lines[unit["start_line"] - 1 : unit["end_line"]]
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"task": ""},
        {"task": "x", "budget": True},
        {"task": "x", "budget": 1},
        {"task": "x", "limit": 0},
        {"task": "x", "limit": 1000},
        {"task": "x", "threshold": "bad"},
    ],
)
def test_invalid_selection_arguments_do_not_call_provider(repo, tmp_path, arguments):
    judge = Judge()
    service = context(repo, tmp_path, judge)
    with pytest.raises(ValueError):
        service.call("select", arguments)
    assert not judge.requests


def test_overlapping_candidates_are_not_duplicated(repo, tmp_path):
    (repo / "auth.py").write_text(
        "class Refresh:\n    def refresh_token(self):\n        return 42\n"
    )
    service = context(repo, tmp_path, Judge())
    response = service.call("select", {"task": "Refresh refresh_token"})
    assert response.count("return 42") == 1


def test_no_candidates_does_not_spend_a_provider_call(repo, tmp_path):
    judge = Judge()
    service = context(repo, tmp_path, judge)
    assert "No matching" in service.call("select", {"task": "xyzzy987unknown"})
    assert not judge.requests
