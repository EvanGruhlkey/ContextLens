from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.evaluation_records import (
    ContextManifestItem,
    EvaluationInvocationRecord,
    InvocationRole,
    append_evaluation_record,
    read_evaluation_records,
)
from contextlens.experiments import (
    AgentOutcome,
    AgentSettings,
    FileChange,
    ReplayResult,
    ReplayStatus,
    ReplayTask,
)
from contextlens.trace import ContextSource, SourceKind


def source(source_id: str, content: str) -> ContextSource:
    return ContextSource(
        source_id=source_id,
        kind=SourceKind.FILE,
        name=f"{source_id}.txt",
        content=content,
        token_count=4,
        token_count_method="provider",
        provenance={"path": f"{source_id}.txt"},
    )


def replay_result() -> ReplayResult:
    return ReplayResult(
        run_id="worker-1",
        task_id="case-1",
        variant_id="without-stale",
        removed_source_ids=("stale",),
        status=ReplayStatus.COMPLETED,
        attempt=2,
        duration_seconds=1.25,
        context_source_ids=("useful",),
        context_tokens=4,
        outcome=AgentOutcome(
            output_text="Fixed the bug.",
            commands=("pytest -q",),
            test_results=("3 passed",),
            input_tokens=120,
            output_tokens=20,
            cost_usd=0.002,
            tool_calls=1,
            retries=1,
            metadata={
                "evaluation_run_id": "eval-1",
                "case_id": "case-1",
                "trial": 3,
                "policy": "contextlens",
                "role": "replay_worker",
                "workspace_id": "workspace-1",
                "rendered_prompt": "Task plus rendered context",
                "reasoning_level": "low",
                "cached_input_tokens": 10,
                "tool_calls": [{"name": "shell", "input": "pytest -q"}],
            },
        ),
        file_changes=(FileChange("src/app.py", "modified", "a", "b"),),
        cache_key="cache-1",
    )


class EvaluationInvocationRecordTests(unittest.TestCase):
    def test_factory_uses_replay_evidence_and_metadata_fallbacks(self) -> None:
        context = (
            source("useful", "Useful current instruction."),
            source("stale", "Old instruction."),
        )
        record = EvaluationInvocationRecord.from_replay_result(
            replay_result(),
            task=ReplayTask("case-1", "Fix the bug."),
            context=context,
            settings=AgentSettings("openai", "small-model", seed=7, temperature=0),
            intervention_id="remove-stale",
            parent_run_id="baseline-1",
        )

        self.assertEqual(record.evaluation_run_id, "eval-1")
        self.assertEqual(record.role, InvocationRole.REPLAY_WORKER)
        self.assertEqual(record.included_context_sources, ("useful",))
        self.assertEqual(record.excluded_context_sources, ("stale",))
        self.assertEqual(record.provider_cached_tokens, 10)
        self.assertEqual(record.provider_input_tokens, 120)
        self.assertEqual(record.changed_files, ("src/app.py",))
        self.assertEqual(record.tool_calls[0]["name"], "shell")
        self.assertEqual(record.reasoning_level, "low")
        self.assertEqual(record.random_seed, 7)
        self.assertEqual(record.retry_count, 1)

    def test_json_round_trip_preserves_the_strict_schema(self) -> None:
        context = (
            source("useful", "Useful current instruction."),
            source("stale", "Old instruction."),
        )
        original = EvaluationInvocationRecord.from_replay_result(
            replay_result(),
            task=ReplayTask("case-1", "Fix the bug."),
            context=context,
            settings=AgentSettings("openai", "small-model"),
            grader_input="anonymous output",
            raw_grader_response='{"score": 1}',
            parsed_score={"score": 1},
        )

        value = json.loads(json.dumps(original.to_dict()))
        restored = EvaluationInvocationRecord.from_dict(value)

        self.assertEqual(restored, original)
        self.assertNotIn("content", restored.context_manifest[0].to_dict())

    def test_jsonl_append_is_durable_and_rejects_duplicate_ids(self) -> None:
        context = (
            source("useful", "Useful current instruction."),
            source("stale", "Old instruction."),
        )
        record = EvaluationInvocationRecord.from_replay_result(
            replay_result(),
            task=ReplayTask("case-1", "Fix the bug."),
            context=context,
            settings=AgentSettings("openai", "small-model"),
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "audit" / "invocations.jsonl"
            append_evaluation_record(path, record)
            self.assertEqual(read_evaluation_records(path), (record,))
            with self.assertRaisesRegex(ValueError, "duplicate evaluation record ID"):
                append_evaluation_record(path, record)

    def test_manifest_partition_and_hashes_are_strict(self) -> None:
        item = ContextManifestItem.from_source(source("only", "Only context."))
        with self.assertRaisesRegex(ValueError, "cover the manifest"):
            EvaluationInvocationRecord(
                evaluation_run_id="eval",
                case_id="case",
                trial=1,
                policy="full",
                role=InvocationRole.BASELINE_WORKER,
                provider="provider",
                model="model",
                started_at="2026-08-03T12:00:00+00:00",
                ended_at="2026-08-03T12:00:01+00:00",
                workspace_id="workspace",
                task_prompt="task",
                rendered_prompt="rendered",
                context_manifest=(item,),
                included_context_sources=(),
                excluded_context_sources=(),
                context_hashes={"only": item.content_hash},
                raw_response="",
                tool_calls=(),
                commands=(),
                changed_files=(),
                test_output=(),
                latency_seconds=1,
                retry_count=0,
                status="completed",
            )

    def test_factory_refuses_missing_raw_tool_calls(self) -> None:
        result = replay_result()
        assert result.outcome is not None
        outcome = AgentOutcome(tool_calls=1)
        missing = ReplayResult(
            run_id=result.run_id,
            task_id=result.task_id,
            variant_id=result.variant_id,
            removed_source_ids=result.removed_source_ids,
            status=result.status,
            attempt=result.attempt,
            duration_seconds=result.duration_seconds,
            context_source_ids=result.context_source_ids,
            context_tokens=result.context_tokens,
            outcome=outcome,
        )
        with self.assertRaisesRegex(ValueError, "raw tool_calls"):
            EvaluationInvocationRecord.from_replay_result(
                missing,
                task=ReplayTask(
                    "case-1",
                    "Fix.",
                    metadata={
                        "evaluation_run_id": "eval",
                        "case_id": "case",
                        "trial": 1,
                        "policy": "full",
                        "role": "baseline_worker",
                        "workspace_id": "workspace",
                        "rendered_prompt": "rendered",
                    },
                ),
                context=(source("useful", "Useful current instruction."),),
                settings=AgentSettings("provider", "model"),
            )


if __name__ == "__main__":
    unittest.main()
