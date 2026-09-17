# ContextLens

**ContextLens helps coding agents find the repository code they need for a task.**
Its goal is to reduce input tokens and irrelevant context while keeping enough
information for the agent to produce a correct result.

For example, a task like “fix the refresh-token timeout” may only need a few
functions and their dependencies. ContextLens finds that code, records where it
came from, and lets the agent recover more of the original source when needed.
It provides context tools for an agent; it does not generate the fix itself.

## How it works

1. Find files and code that match the task.
2. Optionally narrow the context to complete code sections and their dependencies.
3. Keep exact source snapshots so omitted code can be read later.
4. Check source hashes to detect changes before using an old snapshot.

Retrieval runs locally without a GPU or model service. Python is supported,
with optional JavaScript and TypeScript parsing. The MCP server lets agents
retrieve code, read ranges, recover snapshots, and store observations outside
their conversation. Optional neural pruning uses the released SWE-Pruner model.

**Current status:** experimental. Full-file retrieval is the default because the
compressed policy did not preserve task accuracy in the latest benchmark.
Dependency compression requires explicit opt-in.

## Quick start

Requires Git and Python 3.12+. Run these commands from the cloned repository:

```bash
python -m pip install -e ".[evidence]"

contextlens retrieve --root . --task "Fix the refresh-token timeout" --encoding o200k_base
```

The command returns JSON containing source text, file paths, line ranges, hashes,
and recovery IDs. It does not edit your code. Installation currently includes
PyTorch and SWE-Pruner, even when you only use local retrieval.

To try experimental compression:

```bash
contextlens retrieve --root . --task "Fix the refresh-token timeout" --policy dependency --budget 3000 --encoding o200k_base
```

To expose the tools to an MCP-compatible agent:

```bash
contextlens mcp --root . --state .contextlens --encoding o200k_base
```

See the [setup and usage guide](docs/evidence-retrieval.md) for agent configuration,
budgets, recovery, and optional neural pruning.

## Benchmarks

The latest pilot ran **18 real coding-agent attempts**: three repository tasks,
three repeats per task, with full-file and compressed context compared using the
same model and mechanical task checks.

- **Full-file context:** 9 of 9 attempts passed.
- **Compressed context:** 8 of 9 attempts passed.
- **Total model tokens:** compression used **20.8% fewer**, including cached input
  and output tokens across all attempts.

Compression saved tokens overall, but failed one task attempt and used more tokens
on the tslib task. It was also slower at the median. **These results do not show
that compression preserves accuracy**, so it remains experimental.

This is a small pilot on public historical tasks, with focused checks rather than
complete project test suites. No paid API calls were started; the agent runs used
existing subscription capacity and one authorized free reset credit. Dollar
savings are unknown.

Read the [benchmark results](docs/evidence-benchmark.md) or inspect the
[raw report](benchmarks/results/evidence-agent-pilot.json).
[Benchmark instructions](benchmarks/README.md) also cover the separate CPU
retrieval and free-GPU neural-pruning experiments.

## Development

The latest implementation passed **234 automated tests**, lint, and type checks.

```bash
python -m pip install -e ".[dev,evidence]"
python -m pytest -q
ruff check src tests
mypy
```

For more detail, see the [architecture](docs/evidence-architecture.md) and
[research notes](docs/research-context-efficiency-2026-09.md).

Released under the [MIT License](LICENSE).
