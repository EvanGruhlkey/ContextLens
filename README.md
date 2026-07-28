# ContextLens

ContextLens profiles which parts of an AI agent's context appear useful, then
uses targeted experiments to verify which parts actually improve performance.

Most context tooling reports token counts. ContextLens first extracts useful
signals from one normal run, then treats context as an experimental variable.
An adaptive coordinator launches isolated replay workers for the context
changes most likely to matter, rather than blindly rerunning every possible
variant.

```text
Context source             Tokens    Effect
AGENTS.md                    4,820    +14%
Git history                  7,430     +2%
Unused MCP schemas          19,410     -8%
Previous terminal output    11,220     -3%
Architecture decisions       2,180    +21%
```

The intended answer is not merely “what costs tokens?” but:

- Which context improves performance?
- Which context is redundant?
- Which context actively makes the agent worse?

Findings are labeled as **observed**, **predicted**, or **verified**, so a
one-run relevance estimate is never presented as a causal performance claim.

## Project status

ContextLens is in its initial design and implementation phase. The first
milestone defines a provider-neutral trace format and clean boundaries between
recording, replay, evaluation, and reporting. See
[docs/architecture.md](docs/architecture.md),
[docs/trace-format.md](docs/trace-format.md), and [ROADMAP.md](ROADMAP.md).

The trace model and local JSONL recorder are available:

```python
from pathlib import Path
from contextlens.trace import ContextSource, SourceKind, TraceWriter

with TraceWriter(Path("trace.jsonl")) as trace:
    trace.add(
        "request-1",
        ContextSource(
            kind=SourceKind.AGENT_INSTRUCTION,
            name="AGENTS.md",
            content="Run tests.",
        ),
    )
```

The one-run profiler can then extract apparent utilization and duplication
signals without another model call:

```python
from contextlens.profiler import ContextProfiler, RunObservation
from contextlens.trace import TraceReader

events = list(TraceReader("trace.jsonl").events())
report = ContextProfiler().profile(
    events,
    RunObservation(output_text="The completed agent response"),
)
```

Profiler results are always labeled `observed` and `causal: false`. See
[docs/one-run-profiler.md](docs/one-run-profiler.md).

Controlled replays can run context variants concurrently in isolated temporary
workspaces, with limits on attempts, context tokens, estimated cost, and time.
See [docs/replay-workers.md](docs/replay-workers.md).

Adaptive search tests high-value context groups first and splits only groups
whose removal materially changes quality. This reduces unnecessary replay work
while retaining an inspectable decision tree. See
[docs/adaptive-search.md](docs/adaptive-search.md).

## Proposed quick start

The target user experience for the MVP is:

```bash
contextlens record --output traces/task-001.jsonl -- your-agent-command
contextlens run experiments/example.yaml
contextlens report runs/latest
```

These commands document the intended interface; they are not implemented yet.

## Design principles

1. **Local first.** Traces may contain source code, prompts, and command output.
   Nothing leaves the machine unless a configured model provider requires it.
2. **Provider neutral.** OpenAI, Anthropic, local models, and custom agents plug
   into the same trace and replay interfaces.
3. **Reproducible experiments.** Every run records the task, context manifest,
   model settings, evaluator, random seed, and software version.
4. **Adaptive experiments.** Start with cheap one-run signals, test context
   groups in parallel, and spend additional runs only where they add evidence.
5. **Honest statistics.** Reports show evidence level, experiment cost, sample
   sizes, and uncertainty—not just a single percentage.
6. **Extensible context taxonomy.** First-class source kinds cover common agent
   inputs without preventing custom kinds.

## License

ContextLens is released under the [MIT License](LICENSE).
