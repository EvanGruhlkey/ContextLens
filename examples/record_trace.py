"""Record a small ContextLens trace using only the standard library."""

from pathlib import Path

from contextlens.trace import (
    ArtifactStore,
    ContextSource,
    SourceKind,
    TraceWriter,
)

output = Path("example-trace")

with TraceWriter(
    output / "trace.jsonl",
    artifact_store=ArtifactStore(output / "artifacts"),
) as trace:
    trace.add(
        "request-1",
        ContextSource(
            source_id="agents-md",
            kind=SourceKind.AGENT_INSTRUCTION,
            name="AGENTS.md",
            content="Run the test suite before reporting completion.",
            provenance={"path": "AGENTS.md"},
            tags=("repository",),
        ),
    )
    trace.add(
        "request-1",
        ContextSource(
            source_id="user-message-1",
            kind=SourceKind.MESSAGE,
            name="user",
            content="Fix the failing parser test.",
        ),
    )

print(output / "trace.jsonl")

