from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextlens.cli import main, parse_transcript, render_transcript
from contextlens.models import Message, ToolResult, ToolUse
from contextlens.receipts import ReceiptStore


def test_transcript_round_trips() -> None:
    messages = (
        Message("user", "fix it", pinned=True),
        Message("assistant", "", (ToolUse("t1", "grep", {"pattern": "x"}),)),
        Message(
            "user",
            "",
            (),
            (ToolResult("t1", "hit", is_error=True, receipt_id="cl_" + "a" * 24),),
        ),
    )
    assert parse_transcript(render_transcript(messages)) == messages


def test_parse_transcript_rejects_malformed_input() -> None:
    with pytest.raises(ValueError):
        parse_transcript({"role": "user"})
    with pytest.raises(ValueError):
        parse_transcript(["user"])


def test_recover_writes_the_original_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    receipts = ReceiptStore(tmp_path / "receipts")
    receipt = receipts.save("alpha\nbeta\ngamma\n")
    assert (
        main(
            [
                "recover",
                receipt.receipt_id,
                "--receipts",
                str(tmp_path / "receipts"),
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "alpha\nbeta\ngamma\n"


def test_recover_supports_a_line_range(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    receipts = ReceiptStore(tmp_path / "receipts")
    receipt = receipts.save("alpha\nbeta\ngamma\n")
    main(
        [
            "recover",
            receipt.receipt_id,
            "--start-line",
            "2",
            "--end-line",
            "2",
            "--receipts",
            str(tmp_path / "receipts"),
        ]
    )
    assert capsys.readouterr().out == "beta\n"


def test_unknown_receipt_reports_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["recover", "cl_" + "0" * 24, "--receipts", str(tmp_path)]) == 2
    assert "contextlens:" in capsys.readouterr().err


def test_prune_fails_open_without_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("CONTEXTLENS_MIN_TOKENS", "10")
    source = tmp_path / "output.txt"
    output = "\n".join(f"progress line {index}" for index in range(400))
    source.write_text(output, encoding="utf-8")
    assert (
        main(
            [
                "prune",
                "--task",
                "fix the parser",
                "--input",
                str(source),
                "--receipts",
                str(tmp_path / "receipts"),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["text"] == output
    assert payload["reason"] == "jev_unavailable"
    assert payload["receipt_id"]


def test_compact_fails_open_without_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    messages: list[Message] = [Message("user", "fix the parser")]
    for index in range(12):
        identifier = f"t{index}"
        messages.append(
            Message("assistant", "", (ToolUse(identifier, "shell", {"command": "ls"}),))
        )
        messages.append(
            Message("user", "", (), (ToolResult(identifier, "noise\n" * 400),))
        )
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps(render_transcript(tuple(messages))), encoding="utf-8"
    )
    assert (
        main(
            [
                "compact",
                "--transcript",
                str(transcript),
                "--receipts",
                str(tmp_path / "receipts"),
                "--trigger-tokens",
                "100",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["compacted"] is False
    assert payload["reason"] == "jev_unavailable"
    assert parse_transcript(payload["messages"]) == tuple(messages)


def test_compact_honours_threshold_overrides(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps(render_transcript((Message("user", "small"),))), encoding="utf-8"
    )
    main(
        [
            "compact",
            "--transcript",
            str(transcript),
            "--trigger-tokens",
            "1000000",
            "--preserve-recent-messages",
            "2",
            "--receipts",
            str(tmp_path / "receipts"),
            "--json",
        ]
    )
    assert json.loads(capsys.readouterr().out)["reason"] == "below_trigger_tokens"


def test_help_is_available(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
    out = capsys.readouterr().out
    assert "prune" in out and "compact" in out and "recover" in out and "mcp" in out
