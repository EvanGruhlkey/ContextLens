from __future__ import annotations

import json
from pathlib import Path

from contextlens.pruning import PruneRequest, SemanticScores
from contextlens.pruning_cli import main


class _Scorer:
    backend_id = "fixture-v1"

    def score(self, request: PruneRequest) -> SemanticScores:
        return SemanticScores(self.backend_id, {2: 0.99})


def _large_source() -> str:
    lines = ["VALUE = 1", "print(VALUE)"]
    lines.extend(f"unused_{index} = {index}" for index in range(40))
    return "\n".join(lines) + "\n"


def test_prune_command_emits_json_and_saves_receipt(
    tmp_path: Path,
    capsys: object,
) -> None:
    source = tmp_path / "sample.py"
    source.write_text(_large_source(), encoding="utf-8")
    receipts = tmp_path / "receipts"

    exit_code = main(
        [
            "prune",
            "--task",
            "Find displayed value",
            "--input",
            str(source),
            "--minimum-tokens",
            "0",
            "--context-radius",
            "0",
            "--receipts",
            str(receipts),
            "--json",
        ],
        scorer=_Scorer(),
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["backend"] == "fixture-v1"
    assert output["retained_tokens"] < output["original_tokens"]
    assert list(receipts.glob("*.txt"))


def test_recover_command_prints_requested_range(
    tmp_path: Path,
    capsys: object,
) -> None:
    source = tmp_path / "sample.py"
    source.write_text(_large_source(), encoding="utf-8")
    receipts = tmp_path / "receipts"
    main(
        [
            "prune",
            "--task",
            "Find displayed value",
            "--input",
            str(source),
            "--minimum-tokens",
            "0",
            "--receipts",
            str(receipts),
        ],
        scorer=_Scorer(),
    )
    capsys.readouterr()  # type: ignore[attr-defined]
    receipt_id = next(receipts.glob("*.txt")).stem

    exit_code = main(
        [
            "recover",
            receipt_id,
            "--start-line",
            "1",
            "--end-line",
            "2",
            "--receipts",
            str(receipts),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "VALUE = 1\nprint(VALUE)\n"  # type: ignore[attr-defined]


def test_invalid_tool_argument_returns_stable_error(
    tmp_path: Path,
    capsys: object,
) -> None:
    source = tmp_path / "sample.py"
    source.write_text(_large_source(), encoding="utf-8")

    exit_code = main(
        [
            "prune",
            "--task",
            "Inspect",
            "--input",
            str(source),
            "--argument",
            "invalid",
        ],
        scorer=_Scorer(),
    )

    assert exit_code == 2
    assert "key=value" in capsys.readouterr().err  # type: ignore[attr-defined]
