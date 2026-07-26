from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.profiler import (
    ContextProfiler,
    EvidenceLevel,
    RunObservation,
    UsageSignal,
    UsageLabel,
)
from contextlens.trace import (
    ArtifactStore,
    ContextEvent,
    ContextSource,
    SourceKind,
)


def event(
    sequence: int,
    source_id: str,
    name: str,
    content: str,
    *,
    kind: SourceKind = SourceKind.FILE,
    token_count: int | None = None,
    provenance: dict[str, object] | None = None,
) -> ContextEvent:
    return ContextEvent(
        request_id="request-1",
        sequence=sequence,
        source=ContextSource(
            source_id=source_id,
            kind=kind,
            name=name,
            content=content,
            token_count=token_count,
            token_count_method="fixture" if token_count is not None else None,
            provenance=provenance or {},
        ),
    )


class ContextProfilerTests(unittest.TestCase):
    def test_labels_direct_overlap_duplicate_and_unused_sources(self) -> None:
        events = [
            event(0, "instructions", "AGENTS.md", "Always run parser tests first."),
            event(
                1,
                "architecture",
                "architecture.md",
                "The parser uses immutable syntax tree nodes and visitor objects.",
            ),
            event(
                2,
                "architecture-copy",
                "old-notes.md",
                "The parser uses immutable syntax tree nodes and visitor objects.",
            ),
            event(
                3,
                "history",
                "history.txt",
                "A release was prepared last winter by another project team.",
            ),
        ]
        observation = RunObservation(
            output_text=(
                "I updated the parser's immutable syntax tree nodes and ran tests."
            ),
            accessed_source_ids=frozenset({"instructions"}),
        )

        report = ContextProfiler().profile(events, observation)
        profiles = {profile.source_id: profile for profile in report.profiles}

        self.assertEqual(profiles["instructions"].label, UsageLabel.USED)
        self.assertEqual(profiles["architecture"].label, UsageLabel.USED)
        self.assertEqual(profiles["architecture-copy"].label, UsageLabel.USED)
        self.assertEqual(
            profiles["architecture"].duplicated_by,
            ("architecture-copy",),
        )
        self.assertEqual(profiles["history"].label, UsageLabel.UNUSED)
        self.assertTrue(profiles["architecture"].matched_output_spans)
        self.assertTrue(
            all(
                profile.evidence_level is EvidenceLevel.OBSERVED
                for profile in report.profiles
            )
        )

    def test_duplicate_without_usage_is_labeled_duplicated(self) -> None:
        duplicate = "Unused command output with repeated diagnostic details."
        report = ContextProfiler().profile(
            [
                event(0, "first", "first.txt", duplicate),
                event(1, "second", "second.txt", duplicate),
            ],
            RunObservation(output_text="Task completed."),
        )
        self.assertEqual(
            [profile.label for profile in report.profiles],
            [UsageLabel.DUPLICATED, UsageLabel.DUPLICATED],
        )

    def test_supports_semantic_similarity_and_model_internal_adapters(self) -> None:
        class Similarity:
            def score(self, left: str, right: str) -> float:
                return 0.95 if "feline" in left and "cat" in right else 0.0

        class Internals:
            def signals(
                self,
                event: ContextEvent,
                observation: RunObservation,
            ) -> tuple[UsageSignal, ...]:
                return (
                    UsageSignal(
                        "attention_share",
                        0.3,
                        "fixture model attention signal",
                    ),
                )

        report = ContextProfiler(
            content_similarity=Similarity(),
            model_internals=(Internals(),),
        ).profile(
            [
                event(0, "feline", "feline.txt", "feline behavior reference text"),
                event(1, "cat", "cat.txt", "cat conduct background document"),
            ],
            RunObservation(),
        )

        self.assertEqual(report.profiles[0].duplicated_by, ("cat",))
        self.assertEqual(report.profiles[1].duplicated_by, ("feline",))
        self.assertTrue(
            any(
                signal.name == "attention_share"
                for signal in report.profiles[0].signals
            )
        )

    def test_reports_position_age_rank_and_token_totals(self) -> None:
        report = ContextProfiler().profile(
            [
                event(
                    0,
                    "retrieval",
                    "result",
                    "Retrieved information about the requested topic.",
                    kind=SourceKind.RETRIEVAL,
                    token_count=12,
                    provenance={
                        "created_at": "2026-07-26T11:00:00+00:00",
                        "retrieval_rank": 2,
                    },
                ),
                event(1, "message", "user", "Please summarize the topic."),
            ],
            RunObservation(accessed_source_ids=frozenset({"retrieval"})),
            now=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
        )

        first, second = report.profiles
        self.assertEqual(first.position, 0.0)
        self.assertEqual(second.position, 1.0)
        self.assertEqual(first.age_seconds, 3600)
        self.assertEqual(first.retrieval_rank, 2)
        self.assertEqual(first.token_count, 12)
        self.assertGreater(report.total_tokens, 12)
        self.assertFalse(report.to_dict()["causal"])

    def test_unavailable_artifact_is_uncertain(self) -> None:
        with TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory) / "artifacts")
            reference = store.put(b"large externalized context")
            external_event = ContextEvent(
                request_id="request-1",
                sequence=0,
                source=ContextSource(
                    source_id="external",
                    kind=SourceKind.COMMAND_OUTPUT,
                    name="output",
                    content_ref=reference,
                ),
            )

            unavailable = ContextProfiler().profile(
                [external_event],
                RunObservation(),
            )
            available = ContextProfiler(artifact_store=store).profile(
                [external_event],
                RunObservation(output_text="large externalized context"),
            )

            self.assertEqual(unavailable.profiles[0].label, UsageLabel.UNCERTAIN)
            self.assertEqual(available.profiles[0].label, UsageLabel.USED)

    def test_rejects_events_from_multiple_requests(self) -> None:
        first = event(0, "first", "first", "First source content here.")
        second = ContextEvent(
            request_id="request-2",
            sequence=0,
            source=ContextSource(
                source_id="second",
                kind=SourceKind.MESSAGE,
                name="second",
                content="Second source content here.",
            ),
        )
        with self.assertRaisesRegex(ValueError, "exactly one request"):
            ContextProfiler().profile([first, second], RunObservation())


if __name__ == "__main__":
    unittest.main()
