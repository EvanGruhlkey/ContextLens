# ContextLens

ContextLens measures which parts of an AI agent's context actually improve task
performance.

Most context tooling reports token counts. ContextLens treats context as an
experimental variable: record the inputs supplied to an agent, replay
representative tasks, remove one context source at a time, and compare the
result against a full-context baseline.

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
4. **Honest statistics.** Reports show sample sizes and uncertainty, not just a
   single percentage.
5. **Extensible context taxonomy.** First-class source kinds cover common agent
   inputs without preventing custom kinds.

## License

ContextLens is released under the [MIT License](LICENSE).
