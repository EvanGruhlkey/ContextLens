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

**Current status:** experimental. The tests below have not demonstrated total
agent token savings while preserving task accuracy. Full-file retrieval is the
default; compression requires explicit opt-in.

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

### What we tested

**Stopped evaluation:** 85/120 attempts finished on 10 historical bug-fix tasks across AWS Powertools, Luigi, tslib, Click and responses. The agent used `gpt-5.6-luna` with low reasoning effort.

Each run started from a pinned repository checkout. We checked the patch with hidden tests for the bug and selected regressions. Every unmodified checkout failed its checks before the agent attempted a fix.

We compared four approaches:

- **Normal tools:** the agent searches and reads the repository itself.
- **Full files:** ContextLens supplies up to three matching files, with a 30,000-source-token budget.
- **Lexical compression:** ContextLens supplies matching code sections, with a 3,000-source-token budget.
- **Dependency compression:** matching sections plus their static dependencies, with the same 3,000-source-token budget.

### Collected results

| Approach | Passed / finished | Total model tokens | Median time |
| --- | ---: | ---: | ---: |
| Normal tools | 19 / 21 | 5,466,699 | 76.6s |
| ContextLens full files | 18 / 20 | 11,273,242 | 88.5s |
| Lexical compression | 20 / 23 | 7,924,708 | 86.8s |
| Dependency compression | 18 / 21 | 7,618,593 | 79.6s |

Tokens include reported input, cached input and output across each finished attempt, including failed fixes and extra reads. Cached input is included once. Median time includes retrieval, agent execution and the original external checks; checkout and offline regrading are excluded.

Testing stopped at the user's request before all 120 planned runs finished. 2 in-progress attempts were canceled; their usage is unknown and is excluded from this table.

1 invalid run failed the required evidence verification/read workflow despite 1 patch passing the code checks. It does not count as a pass. Its reported tokens remain in the table.

### What we learned

The conditions have different numbers of finished runs. To compare token usage fairly, we matched runs on the same task and trial:

- **Full files:** 114.2% more tokens than normal tools over 16 valid matched pairs.
- **Lexical compression:** 40.7% more tokens than normal tools over 16 valid matched pairs.
- **Dependency compression:** 35.1% more tokens than normal tools over 14 valid matched pairs.

**These runs do not demonstrate total token savings over normal tools.** We also observed correct-to-incorrect fix regressions in matched runs. Smaller source context alone does not establish cheaper or equally accurate agent execution.

This is a small sample of public historical tasks using one model, not a held-out benchmark or full project test suites. ContextLens runs must verify evidence and read a range, which adds workflow overhead. Different budgets also affect the comparison. This evaluation does not establish quality for optional neural pruning or long-conversation memory.

No paid API calls were started. The runs used existing subscription capacity and authorized free reset credits; dollar savings are unknown.

### Earlier free-GPU runtime test

An earlier source snapshot was tested on a free Colab T4: three files, three reads each. This measures returned-text size and runtime, rather than bug-fix accuracy or total agent tokens.

| Neural runtime | Backend failures | Returned-text reduction | First read | Warm median |
| --- | ---: | ---: | ---: | ---: |
| Default (invalid run) | 4/9 | 25.62% | 45.99s | 2.56s |
| Experimental efficient SDPA | 0/9 | 75.45% | 17.69s | 2.83s |

The default ran out of GPU memory. The experimental variant's numerical equivalence and agent-quality preservation are unverified.

[Per-task results, methods and uncertainty](docs/comprehensive-benchmark.md) · [JSON results](benchmarks/results/comprehensive.json) · [CSV results](benchmarks/results/comprehensive.csv) · [Reproduce the runs](benchmarks/README.md) · [GPU runtime audit](docs/benchmark-audit.md)

## Development

The most recent checks passed **239 automated tests**, lint, and type checks.

```bash
python -m pip install -e ".[dev,evidence]"
python -m pytest -q
ruff check src tests
mypy
```

For more detail, see the [architecture](docs/evidence-architecture.md) and
[research notes](docs/research-context-efficiency-2026-09.md).

Released under the [MIT License](LICENSE).
