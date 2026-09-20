import subprocess
from pathlib import Path

from benchmarks.jev_selection import Case, measure_case
from contextlens.jev_gateway import Evaluation


class RelevantJudge:
    def evaluate(self, state, questions):
        probabilities = {
            name: 0.95
            if "refresh_token" in candidate["source"]
            or "TIMEOUT =" in candidate["source"]
            or candidate["role"] == "support"
            else 0.05
            for name, candidate in state["candidates"].items()
        }
        return Evaluation(
            probabilities,
            "typesafe-ai/jev",
            100,
            10,
            "0.00001",
            12.0,
        )


def test_measure_case_reports_evidence_and_total_selection_cost(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "auth.py").write_text(
        "TIMEOUT = 30\n\ndef refresh_token():\n    return TIMEOUT\n"
        "\ndef unrelated():\n    return 'noise'\n"
        + "".join(f"UNRELATED_{index} = {index}\n" for index in range(100)),
        encoding="utf-8",
    )
    case = Case(
        "refresh-timeout",
        root,
        "auth.py",
        "find refresh_token timeout behavior",
        "refresh_token",
        ("def refresh_token", "TIMEOUT = 30"),
        ("return 'noise'",),
    )

    row = measure_case(case, tmp_path / "state", RelevantJudge())
    assert row["evidence_check"] == "passed", row
    assert row["required_anchors_found"] == 2
    assert row["forbidden_anchors_found"] == 0
    assert row["selection_response_tokens"] < row["full_read_tokens"]
    assert row["gateway_input_tokens"] == 100
    assert row["gateway_output_tokens"] == 10
    assert row["gateway_cost"] == "0.00001"
    assert row["gateway_latency_ms"] == 12.0
