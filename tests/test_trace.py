from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from contextlens.trace import (
    ArtifactStore,
    ContextSource,
    RegexRedactor,
    SourceKind,
    TraceReader,
    TraceWriter,
)


class ContextSourceTests(unittest.TestCase):
    def test_requires_exactly_one_content_representation(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            ContextSource(kind=SourceKind.FILE, name="empty")

    def test_token_method_is_required_with_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "token_count_method"):
            ContextSource(
                kind=SourceKind.MESSAGE,
                name="user",
                content="hello",
                token_count=2,
            )


class TraceRoundTripTests(unittest.TestCase):
    def test_round_trip_preserves_order_and_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            first = ContextSource(
                source_id="instructions",
                kind=SourceKind.AGENT_INSTRUCTION,
                name="AGENTS.md",
                content="Run tests.",
                token_count=3,
                token_count_method="fixture",
                provenance={"path": "AGENTS.md"},
                tags=("repo",),
            )
            second = ContextSource(
                source_id="message",
                kind=SourceKind.MESSAGE,
                name="user",
                content="Fix the bug.",
            )

            with TraceWriter(path) as writer:
                writer.add("request-1", first)
                writer.add("request-1", second)

            reader = TraceReader(path)
            events = list(reader.events())

            self.assertEqual(reader.read_header().schema_version, "1.0")
            self.assertEqual([event.sequence for event in events], [0, 1])
            self.assertEqual(events[0].source, first)
            self.assertEqual(events[1].source, second)

    def test_large_content_is_externalized_and_verified(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "trace.jsonl"
            artifacts = ArtifactStore(root / "artifacts")
            source = ContextSource(
                kind=SourceKind.COMMAND_OUTPUT,
                name="test output",
                content="abcdef",
            )

            with TraceWriter(
                path,
                artifact_store=artifacts,
                artifact_threshold=1,
            ) as writer:
                event = writer.add("request-1", source)

            self.assertIsNone(event.source.content)
            self.assertIsNotNone(event.source.content_ref)
            assert event.source.content_ref is not None
            self.assertEqual(artifacts.get(event.source.content_ref), b"abcdef")

    def test_redaction_happens_before_persistence(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            source = ContextSource(
                kind=SourceKind.MESSAGE,
                name="user",
                content="token=secret",
            )

            with TraceWriter(
                path,
                redactors=(RegexRedactor(r"secret"),),
            ) as writer:
                event = writer.add("request-1", source)

            self.assertEqual(event.source.content, "token=[REDACTED]")
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))

    def test_reader_rejects_out_of_order_events(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            with TraceWriter(path) as writer:
                writer.add(
                    "request-1",
                    ContextSource(
                        kind=SourceKind.MESSAGE,
                        name="user",
                        content="hello",
                    ),
                )
            lines = path.read_text(encoding="utf-8").splitlines()
            event = json.loads(lines[1])
            event["sequence"] = 4
            lines[1] = json.dumps(event)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "expected sequence 0"):
                list(TraceReader(path).events())


if __name__ == "__main__":
    unittest.main()

