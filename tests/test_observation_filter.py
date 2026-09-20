from pathlib import Path

from contextlens.filtering import (
    FilterConfig,
    FilterRequest,
    FilterSession,
    ObservationFilter,
)
from contextlens.jev_gateway import Evaluation
from contextlens.observations import ObservationStore
from contextlens.pruning.model import ObservationKind
from contextlens.pruning.receipts import ReceiptStore
from contextlens.pruning.runtime import ToolObservation


class Judge:
    def __init__(self, choose):
        self.choose = choose
        self.requests = []

    def evaluate(self, state, questions):
        self.requests.append((state, questions))
        return Evaluation(
            {key: self.choose(key, state["candidates"][key]) for key in questions},
            "typesafe-ai/jev",
            12,
            3,
            "0",
            1.0,
        )


SOURCE = """TIMEOUT = 30

def refresh_token():
    return TIMEOUT

def create_session():
    return "session"

def validate_refresh_token():
    return True

def logout():
    return "bye"
""" + "".join(f"\ndef unused_{index}():\n    return {index}\n" for index in range(30))


def _filter(tmp_path: Path, judge, **kwargs) -> ObservationFilter:
    settings = {"minimum_tokens": 0, **kwargs}
    return ObservationFilter(
        ReceiptStore(tmp_path / "receipts"),
        judge=judge,
        config=FilterConfig(**settings),
    )


def test_source_filter_keeps_relevant_code_and_structural_support(tmp_path: Path):
    def choose(_key, candidate):
        source = candidate.get("source", "")
        return 0.9 if "refresh_token" in source else 0.1

    result = _filter(tmp_path, Judge(choose)).filter(
        FilterRequest(
            task="fix the refresh-token timeout",
            content=SOURCE,
            tool="read_file",
            arguments={"path": "auth.py"},
            kind=ObservationKind.CODE,
            path="auth.py",
        )
    )

    assert "def refresh_token():" in result.text
    assert "TIMEOUT = 30" in result.text
    assert "def logout():" not in result.text
    assert "omitted" in result.text
    original = ReceiptStore(tmp_path / "receipts").read(result.receipt_id)
    assert "def logout():" in original
    assert result.jev_input_tokens == 12
    assert result.retained_tokens < result.original_tokens


def test_search_filter_drops_unrelated_hits(tmp_path: Path):
    output = "\n".join(
        [
            "auth.py:3:def refresh_token():",
            "auth.py:10:def logout():",
            "notes.md:4:refresh the UI theme",
        ]
    )
    result = _filter(
        tmp_path,
        Judge(lambda _key, cand: 0.9 if "refresh_token" in cand["text"] else 0.1),
    ).filter(
        FilterRequest(
            task="fix refresh token",
            content=output,
            tool="rg",
            kind=ObservationKind.SEARCH,
        )
    )
    assert "def refresh_token():" in result.text
    assert "logout" not in result.text
    assert "UI theme" not in result.text


def test_test_output_never_drops_the_only_failure(tmp_path: Path):
    output = """
============================= test session starts ==============================
collected 2 items
passed setup noise
=========================== FAILURES ===========================================
______________________________ test_refresh _________________________________
AssertionError: assert 900 == 3600
test_auth.py:12: AssertionError
=========================== short test summary info ============================
FAILED test_auth.py::test_refresh - AssertionError
passed leftover logs
"""
    result = _filter(tmp_path, Judge(lambda *_: 0.0)).filter(
        FilterRequest(
            task="fix refresh timeout",
            content=output,
            tool="pytest",
            kind=ObservationKind.TEST,
        )
    )
    assert "AssertionError" in result.text
    assert "FAILED test_auth.py::test_refresh" in result.text


def test_small_and_narrow_reads_bypass_jev(tmp_path: Path):
    judge = Judge(lambda *_: 0.9)
    small = _filter(tmp_path, judge, minimum_tokens=256).filter(
        FilterRequest(task="task", content="x = 1\n", tool="read_file")
    )
    narrow = _filter(tmp_path, judge).filter(
        FilterRequest(
            task="task",
            content=SOURCE * 8,
            tool="read_file",
            start_line=1,
            end_line=4,
        )
    )
    assert small.bypass_reason == "below_minimum_tokens"
    assert narrow.bypass_reason == "narrow_line_range"
    assert judge.requests == []


def test_session_can_pin_and_recover_deferred_observations(tmp_path: Path):
    session = FilterSession(
        ReceiptStore(tmp_path / "receipts"),
        ObservationStore(tmp_path / "obs"),
        task="fix refresh timeout",
        judge=Judge(lambda *_: 0.2),
        config=FilterConfig(minimum_tokens=0),
    )
    result = session.observe(
        ToolObservation(SOURCE, "read_file", {"path": "auth.py"}, ObservationKind.CODE)
    )
    listing = session.listing()
    handle = listing["active"][0]["id"]
    pinned = session.pin(handle)
    session.collect_garbage()
    assert pinned.pinned
    assert session.listing()["pinned"][0]["id"] == handle
    recovered = session.recover(result.receipt_id)
    assert recovered == SOURCE
    assert session.recovery_calls == 1
