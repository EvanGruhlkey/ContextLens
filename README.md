# ContextLens

ContextLens profiles which parts of an AI agent's context appear useful, then
uses targeted experiments to verify which parts actually improve performance.

Most context tooling reports token counts. ContextLens first extracts useful
signals from one normal run, then treats context as an experimental variable.
An adaptive coordinator launches isolated replay workers for the context
changes most likely to matter, rather than blindly rerunning every possible
variant.

```text
Context source             Tokens   Effect   Action       Token savings
AGENTS.md                    4,820    +14%    Keep                     0
Git history                  7,430     +2%    Investigate              0
Unused MCP schemas          19,410     -8%    Remove       1,941,000,000
Previous terminal output    11,220     -3%    Remove       1,122,000,000
Architecture decisions       2,180    +21%    Keep                     0
```

The illustrative savings column assumes 100,000 production runs. Real reports
use measured per-run usage and the agent's actual workload.

The intended answer is not merely “what costs tokens?” but:

- Which context improves performance?
- Which context is redundant?
- Which context actively makes the agent worse?
- How many tokens, dollars, and seconds can safely be saved in production?

Findings are labeled as **observed**, **predicted**, or **verified**, so a
one-run relevance estimate is never presented as a causal performance claim.

## Project status

ContextLens is a working pre-alpha library and CLI. Recording, one-run
profiling, isolated replay, adaptive search, paired analysis, optimization, and
multi-format reports are implemented. See
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

Paired analysis compares matched baseline and ablated trials, calculates
bootstrap uncertainty, warns about unstable evidence, and reports quality,
success, token, cost, and latency effects. See
[docs/evaluation-and-analysis.md](docs/evaluation-and-analysis.md).

Context optimization combines safe removals, optionally screens them with
fixed-answer scoring, and verifies the complete configuration on the target
model. A lightweight predictor learns from verified experiments while periodic
recalibration prevents predictions from silently becoming trusted facts. See
[docs/context-optimization.md](docs/context-optimization.md).

Verified effects can be converted into keep/remove/investigate decisions and
projected across a real production workload, including experiment amortization
and break-even runs. See
[docs/production-savings.md](docs/production-savings.md).

The deterministic planner benchmark uses 12 experiments instead of 33
exhaustive runs on its 32-source fixture while retaining the critical source.
This is a planner test, not a claim about expected savings on real model tasks.
See [benchmarks/README.md](benchmarks/README.md).

## Quick start

The CLI workflows are available:

```bash
contextlens record --output traces/task-001.jsonl -- your-agent-command
contextlens scan traces/task-001.jsonl
contextlens optimize experiment.json --format json --output runs/latest.json
contextlens report runs/latest.json --format html --output runs/latest.html
```

The agent used with `record` must write the path provided in
`CONTEXTLENS_TRACE`. See [docs/cli.md](docs/cli.md) for the instrumentation and
subprocess worker contracts.

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
