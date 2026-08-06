from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.experiments.codex_cli import (
    CodexCliAgentAdapter,
    CodexCliExecutionError,
    render_codex_prompt,
)
from contextlens.experiments.model import (
    AgentSettings,
    ContextVariant,
    ReplayRequest,
    ReplayTask,
)
from contextlens.trace.model import ContextSource, SourceKind


def _request(workspace: Path, *, timeout: float = 10) -> ReplayRequest:
    return ReplayRequest(
        run_id="run-one",
        task=ReplayTask("task-one", "Fix the parser and run its tests."),
        variant=ContextVariant("full"),
        context=(
            ContextSource(
                source_id="instructions",
                kind=SourceKind.AGENT_INSTRUCTION,
                name='AGENTS "root".md',
                content="Use Python 3.11.\nDo not change the public API.",
            ),
            ContextSource(
                source_id="parser",
                kind=SourceKind.FILE,
                name="src/parser.py",
                content="def parse(value):\n    return None\n",
            ),
        ),
        settings=AgentSettings(
            provider="openai",
            model="gpt-test",
            seed=7,
            temperature=0,
            tools=("shell",),
            parameters={
                "reasoning_effort": "low",
                "sandbox": "read-only",
                "unrecognized": "kept-as-evidence",
            },
        ),
        workspace=str(workspace),
        timeout_seconds=timeout,
    )


class CodexCliAgentAdapterTests(unittest.TestCase):
    def test_fresh_exec_receives_exact_prompt_and_parses_jsonl(self) -> None:
        script_text = """
import json
import pathlib
import sys

prompt = sys.stdin.read()
pathlib.Path("invocation.json").write_text(
    json.dumps({"argv": sys.argv[1:], "prompt": prompt}), encoding="utf-8"
)
events = [
    {"type": "thread.started", "thread_id": "thread-123"},
    {
        "type": "item.completed",
        "item": {
            "id": "cmd-1",
            "type": "command_execution",
            "command": "python -m pytest",
            "aggregated_output": "2 passed",
            "exit_code": 0,
            "status": "completed",
        },
    },
    {
        "type": "item.completed",
        "item": {
            "id": "tool-1",
            "type": "mcp_tool_call",
            "server": "fixture",
            "tool": "lookup",
            "status": "completed",
        },
    },
    {
        "type": "item.completed",
        "item": {
            "id": "message-1",
            "type": "agent_message",
            "text": "Fixed the parser; tests pass.",
        },
    },
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 321,
            "cached_input_tokens": 21,
            "output_tokens": 45,
        },
    },
]
for event in events:
    print(json.dumps(event), flush=True)
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            script = root / "fake_codex.py"
            script.write_text(script_text, encoding="utf-8")
            request = _request(workspace)
            adapter = CodexCliAgentAdapter((sys.executable, str(script)))

            outcome = adapter.run(request)

            invocation = json.loads(
                (workspace / "invocation.json").read_text(encoding="utf-8")
            )
            expected_prompt = render_codex_prompt(request)
            self.assertEqual(invocation["prompt"], expected_prompt)
            self.assertEqual(
                invocation["argv"],
                [
                    "exec",
                    "--ephemeral",
                    "--json",
                    "--skip-git-repo-check",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--model",
                    "gpt-test",
                    "--sandbox",
                    "read-only",
                    "-c",
                    'windows.sandbox="elevated"',
                    "-c",
                    'shell_environment_policy.set.PYTHONDONTWRITEBYTECODE="1"',
                    "-c",
                    'model_reasoning_effort="low"',
                    "-",
                ],
            )
            self.assertEqual(
                expected_prompt.count("<task task_id="),
                1,
            )
            for source in request.context:
                assert source.content is not None
                self.assertIn(source.content, expected_prompt)

            self.assertEqual(outcome.output_text, "Fixed the parser; tests pass.")
            self.assertEqual(outcome.commands, ("python -m pytest",))
            self.assertEqual(outcome.tool_calls, 2)
            self.assertEqual(outcome.input_tokens, 321)
            self.assertEqual(outcome.cached_input_tokens, 21)
            self.assertEqual(outcome.output_tokens, 45)
            self.assertEqual(outcome.metadata["thread_id"], "thread-123")
            self.assertEqual(outcome.metadata["cached_input_tokens"], 21)
            self.assertEqual(outcome.metadata["rendered_prompt"], expected_prompt)
            self.assertIn('"type": "turn.completed"', outcome.metadata["raw_jsonl"])
            self.assertEqual(len(outcome.metadata["command_events"]), 1)
            self.assertEqual(len(outcome.metadata["tool_events"]), 1)
            self.assertEqual(
                [
                    event["item"]["id"]
                    for event in outcome.metadata["tool_calls"]
                ],
                ["cmd-1", "tool-1"],
            )
            self.assertEqual(outcome.metadata["returncode"], 0)
            self.assertGreaterEqual(outcome.metadata["latency_ms"], 0)
            self.assertTrue(outcome.metadata["started_at"].endswith("+00:00"))
            self.assertTrue(outcome.metadata["ended_at"].endswith("+00:00"))
            self.assertEqual(
                outcome.metadata["unsupported_settings"],
                {
                    "seed": 7,
                    "temperature": 0,
                    "tools": ["shell"],
                    "parameters": {"unrecognized": "kept-as-evidence"},
                },
            )

    def test_jsonl_parse_and_agent_errors_are_preserved(self) -> None:
        script_text = """
import json

print("not-json")
print(json.dumps({
    "type": "item.failed",
    "item": {"type": "mcp_tool_call", "status": "failed", "error": "boom"},
}))
print(json.dumps({
    "type": "item.completed",
    "item": {"type": "agent_message", "text": "Recovered."},
}))
print(json.dumps({
    "type": "turn.completed",
    "usage": {
        "input_tokens": 10,
        "input_tokens_details": {"cached_tokens": 4},
        "output_tokens": 2,
    },
}))
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            script = root / "fake_codex.py"
            script.write_text(script_text, encoding="utf-8")

            outcome = CodexCliAgentAdapter(
                (sys.executable, str(script))
            ).run(_request(workspace))

            self.assertEqual(outcome.output_text, "Recovered.")
            self.assertEqual(outcome.metadata["errors"], ["boom"])
            self.assertEqual(len(outcome.metadata["jsonl_parse_errors"]), 1)
            self.assertEqual(outcome.metadata["cached_input_tokens"], 4)
            self.assertEqual(outcome.cached_input_tokens, 4)

    def test_empty_terminal_message_preserves_prior_nonempty_message(self) -> None:
        script_text = """
import json

for event in (
    {"type": "item.completed", "item": {
        "type": "agent_message", "text": "Wrote the requested artifact."
    }},
    {"type": "item.completed", "item": {
        "type": "file_change", "status": "completed"
    }},
    {"type": "item.completed", "item": {
        "type": "agent_message", "text": ""
    }},
    {"type": "turn.completed", "usage": {
        "input_tokens": 8, "cached_input_tokens": 2, "output_tokens": 3
    }},
):
    print(json.dumps(event))
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            script = root / "fake_codex.py"
            script.write_text(script_text, encoding="utf-8")

            outcome = CodexCliAgentAdapter(
                (sys.executable, str(script))
            ).run(_request(workspace))

            self.assertEqual(outcome.output_text, "Wrote the requested artifact.")
            self.assertFalse(outcome.metadata["empty_final_message"])

    def test_file_only_success_may_have_empty_final_message(self) -> None:
        script_text = """
import json

for event in (
    {"type": "item.completed", "item": {
        "type": "file_change", "status": "completed"
    }},
    {"type": "item.completed", "item": {
        "type": "agent_message", "text": ""
    }},
    {"type": "turn.completed", "usage": {
        "input_tokens": 8, "cached_input_tokens": 2, "output_tokens": 3
    }},
):
    print(json.dumps(event))
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            script = root / "fake_codex.py"
            script.write_text(script_text, encoding="utf-8")

            outcome = CodexCliAgentAdapter(
                (sys.executable, str(script))
            ).run(_request(workspace))

            self.assertEqual(outcome.output_text, "")
            self.assertTrue(outcome.metadata["empty_final_message"])
            self.assertEqual(outcome.metadata["errors"], [])

    def test_nonzero_exit_raises_with_complete_raw_evidence(self) -> None:
        script_text = """
import json
import sys

print(json.dumps({"type": "thread.started", "thread_id": "failed-thread"}))
print(json.dumps({"type": "error", "message": "provider unavailable"}))
print("diagnostic", file=sys.stderr)
raise SystemExit(7)
"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            script = root / "fake_codex.py"
            script.write_text(script_text, encoding="utf-8")

            with self.assertRaises(CodexCliExecutionError) as captured:
                CodexCliAgentAdapter((sys.executable, str(script))).run(
                    _request(workspace)
                )

            error = captured.exception
            self.assertEqual(error.returncode, 7)
            self.assertEqual(error.metadata["thread_id"], "failed-thread")
            self.assertIn("provider unavailable", error.metadata["errors"])
            self.assertIn("diagnostic", error.metadata["stderr"])
            self.assertIn('"type": "error"', error.metadata["raw_jsonl"])
            self.assertEqual(error.metadata["returncode"], 7)


if __name__ == "__main__":
    unittest.main()
