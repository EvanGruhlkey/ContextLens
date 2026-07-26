# Isolated replay workers

Replay workers test a context intervention while holding the rest of an agent
task constant.

## Reproducibility boundary

A `ReplayWorker` binds:

- One task and immutable starting directory snapshot.
- One ordered set of context sources.
- One agent adapter.
- Provider, model, seed, temperature, tools, and additional parameters.
- A per-attempt timeout.

A `ContextVariant` changes only the set of removed source IDs. Every attempt
receives a fresh temporary copy of the starting directory. Git metadata, caches,
and Python bytecode are excluded from copies.

```python
from pathlib import Path

from contextlens.experiments import (
    AgentSettings,
    ContextVariant,
    DirectorySnapshot,
    ReplayCoordinator,
    ReplayTask,
    ReplayWorker,
    ResourceLimits,
    SubprocessAgentAdapter,
)

worker = ReplayWorker(
    adapter=SubprocessAgentAdapter(("my-agent-wrapper",)),
    snapshot=DirectorySnapshot(Path("task-fixture")),
    task=ReplayTask("parser-1", "Fix the parser."),
    context=context_sources,
    settings=AgentSettings(
        provider="example",
        model="example-model",
        seed=42,
        temperature=0,
    ),
    timeout_seconds=300,
)

results = ReplayCoordinator(
    worker,
    ResourceLimits(
        max_workers=4,
        max_runs=20,
        max_context_tokens=500_000,
        max_estimated_cost_usd=10,
    ),
).run(
    (
        ContextVariant("baseline", estimated_cost_usd=0.50),
        ContextVariant(
            "without-history",
            frozenset({"git-history"}),
            estimated_cost_usd=0.40,
        ),
    )
)
```

## Subprocess contract

`SubprocessAgentAdapter` sets `CONTEXTLENS_REQUEST` to an absolute JSON file
containing the task, selected context, model settings, workspace, variant, and
timeout. It runs the configured command with the isolated workspace as its
working directory.

Standard output becomes the agent output. A nonzero exit becomes a failed
result, and exceeding the timeout becomes a timed-out result. Custom adapters
must honor the timeout included in `ReplayRequest`.

## Captured evidence

Each result preserves:

- Status, attempt, duration, and error.
- Selected and removed context source IDs.
- Context token count.
- Agent output, commands, test results, usage, cost, and adapter metadata.
- Created, modified, and deleted files.
- SHA-256 before/after digests and bounded unified text patches.
- A content-derived cache key.

`MemoryReplayCache` avoids rerunning an equivalent task, context, model,
adapter, and workspace snapshot during the same process.

## Resource controls

The coordinator rejects an experiment before execution when it can exceed:

- Maximum concurrent workers.
- Maximum possible attempts, including retries.
- Cumulative planned context tokens.
- Cumulative estimated cost.

Variants require explicit cost estimates whenever a cost ceiling is enabled.
Parallel completion order never changes the returned variant order.

## Evaluation boundary

`Evaluator` and `Evaluation` define how later milestones attach numeric scores
and inspectable evidence to completed replays. Evaluators do not mutate the
worker workspace.

