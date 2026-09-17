"""Harness integrity tests use fakes; they are not model benchmark results."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from contextlens.pruning import PruneRequest, SemanticScores


@pytest.mark.parametrize("broken", [False, True])
def test_runtime_benchmark_does_not_count_backend_failure_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    broken: bool,
) -> None:
    pytest.importorskip("tiktoken")
    from benchmarks import pruning_runtime

    class Scorer:
        backend_id = "fixture-not-real-model"

        def score(self, request: PruneRequest) -> SemanticScores:
            if broken:
                raise RuntimeError("offline")
            return SemanticScores(self.backend_id, {1: 1.0})

    monkeypatch.setattr(pruning_runtime, "HttpSemanticScorer", lambda *a, **k: Scorer())
    monkeypatch.setattr(pruning_runtime, "CASES", pruning_runtime.CASES[:1])
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark",
            "--backend",
            "http",
            "--repeats",
            "2",
            "--output",
            str(output),
        ],
    )
    status = pruning_runtime.main()
    report = json.loads(output.read_text())
    assert status == (1 if broken else 0)
    assert report["status"] == ("invalid" if broken else "complete")
    assert report["summary"]["failed_observations"] == (2 if broken else 0)
    assert report["agent_quality_measured"] is False
    assert report["provider_usage_measured"] is False
    assert report["production_savings_claim"] is None
    assert [row["first_request"] for row in report["cases"]] == [True, False]
    import tiktoken

    encoding = tiktoken.get_encoding(report["encoding"])
    for row in report["cases"]:
        assert row["retained_tokens"] == len(
            encoding.encode(row["output"], disallowed_special=())
        )


def test_structural_audit_is_explicitly_not_a_neural_benchmark() -> None:
    pytest.importorskip("tiktoken")
    from benchmarks.pruning_quality import run

    report = run()
    assert report["agent_quality_measured"] is False
    assert report["neural_model_used"] is False
    assert report["summary"] == {
        "cases": 7,
        "support_passes": 7,
        "pruned_cases": 7,
        "exact_recovery_cases": 3,
    }
