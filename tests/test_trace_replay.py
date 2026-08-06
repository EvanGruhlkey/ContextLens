from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.experiments import (
    AgentOutcome,
    AgentSettings,
    FileChange,
    ReplayResult,
    ReplayStatus,
    ReplayTask,
)
from contextlens.trace import (
    AgentStatus,
    ContextSource,
    RecordedReplayTrace,
    SourceKind,
    StepType,
    TraceReader,
    record_replay_trace,
)


def source(source_id: str) -> ContextSource:
    return ContextSource(
        source_id=source_id,
        kind=SourceKind.FILE,
        name=f"{source_id}.txt",
        content=f"Content for {source_id}.",
        token_count=4,
        token_count_method="provider",
    )


class ReplayTraceTests(unittest.TestCase):
    def test_records_real_replay_context_steps_usage_and_observation(self) -> None:
        context = (source("included"), source("excluded"))
        outcome = AgentOutcome(
            output_text="Implemented the fix.",
            commands=("pytest -q",),
            test_results=("4 passed",),
            input_tokens=120,
            cached_input_tokens=20,
            output_tokens=15,
            tool_calls=1,
            metadata={
                "rendered_prompt": "Exact rendered prompt",
                "raw_model_response": "Implemented the fix.",
                "raw_jsonl": '{"type":"turn.completed"}\n',
            },
        )
        result = ReplayResult(
            run_id="run-1",
            task_id="case-1",
            variant_id="without-excluded",
            removed_source_ids=("excluded",),
            status=ReplayStatus.COMPLETED,
            attempt=1,
            duration_seconds=1.25,
            context_source_ids=("included",),
            context_tokens=4,
            outcome=outcome,
            file_changes=(FileChange("src/app.py", "modified", "a", "b"),),
            workspace_id="workspace-1",
            started_at="2026-08-03T12:00:00+00:00",
            ended_at="2026-08-03T12:00:01.250000+00:00",
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            recorded = record_replay_trace(
                path,
                task=ReplayTask("case-1", "Fix the bug."),
                context=context,
                settings=AgentSettings("openai", "small-model"),
                result=result,
            )

            self.assertIsInstance(recorded, RecordedReplayTrace)
            reader = TraceReader(path)
            trace = reader.read_trace()
            assert trace is not None
            self.assertEqual(trace.status, AgentStatus.COMPLETED)
            self.assertEqual(trace.total_input_tokens, 120)
            self.assertEqual(trace.total_cached_tokens, 20)
            self.assertEqual(trace.total_output_tokens, 15)
            self.assertEqual(trace.total_tool_calls, 1)
            self.assertEqual(
                tuple(event.source.source_id for event in reader.events()),
                ("included",),
            )
            steps = tuple(reader.steps())
            self.assertEqual(
                tuple(step.step_type for step in steps),
                (
                    StepType.MODEL_REQUEST,
                    StepType.TOOL_CALL,
                    StepType.MODEL_RESPONSE,
                    StepType.EVALUATION,
                ),
            )
            self.assertEqual(steps[0].content, "Exact rendered prompt")
            self.assertEqual(
                steps[2].metadata["raw_model_response"],
                "Implemented the fix.",
            )
            self.assertIn("turn.completed", steps[2].metadata["raw_jsonl"])
            self.assertEqual(recorded.observation.commands, ("pytest -q",))
            self.assertEqual(recorded.observation.changed_files, ("src/app.py",))
            self.assertEqual(recorded.observation.task_text, "Fix the bug.")

    def test_failed_replay_still_produces_a_valid_trace(self) -> None:
        item = source("only")
        result = ReplayResult(
            run_id="run-failed",
            task_id="case",
            variant_id="baseline",
            removed_source_ids=(),
            status=ReplayStatus.TIMED_OUT,
            attempt=1,
            duration_seconds=3,
            context_source_ids=("only",),
            context_tokens=4,
            error="timeout",
            workspace_id="workspace-failed",
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            recorded = record_replay_trace(
                path,
                task=ReplayTask("case", "Complete task."),
                context=(item,),
                settings=AgentSettings("provider", "model"),
                result=result,
            )

            trace = TraceReader(path).read_trace()
            assert trace is not None
            self.assertEqual(trace.status, AgentStatus.FAILED)
            self.assertEqual(
                tuple(step.step_type for step in TraceReader(path).steps()),
                (StepType.MODEL_REQUEST,),
            )
            self.assertEqual(recorded.observation.output_text, "")

    def test_rejects_an_incomplete_context_manifest(self) -> None:
        result = ReplayResult(
            run_id="run",
            task_id="case",
            variant_id="baseline",
            removed_source_ids=(),
            status=ReplayStatus.COMPLETED,
            attempt=1,
            duration_seconds=1,
            context_source_ids=("missing",),
            context_tokens=1,
        )
        with (
            TemporaryDirectory() as directory,
            self.assertRaisesRegex(ValueError, "missing from the manifest"),
        ):
            record_replay_trace(
                Path(directory) / "trace.jsonl",
                task=ReplayTask("case", "Complete task."),
                context=(source("known"),),
                settings=AgentSettings("provider", "model"),
                result=result,
            )


if __name__ == "__main__":
    unittest.main()
