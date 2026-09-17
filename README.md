# ContextLens

**ContextLens helps coding agents find the repository code they need for a task.**
Its goal is to reduce input tokens and irrelevant context while keeping enough
information for the agent to produce a correct result.

For example, a task like “fix the refresh-token timeout” may only need a few
functions and their dependencies. ContextLens finds that code, records where it
came from, and lets the agent recover more of the original source when needed.
It provides context tools for an agent; it does not generate the fix itself.

## How it works

1. **Find locations:** search returns short handles and code locations, without eagerly adding file bodies.
2. **Read evidence:** request a handle or a known path. Reads return exact code with its statically identified support and check freshness internally.
3. **Recover safely:** original snapshots remain local. Historical expansion is clearly marked; changed source requires a fresh read.
4. **Control what enters history:** the optional callback adapter transforms registered tool observations before sending them to the solver.

Python methods and nested functions are indexed individually. Optional JavaScript
and TypeScript parsing provides similar granularity. Support resolution is bounded
and static; unresolved dependencies are reported. Oversized evidence groups are
refused rather than silently truncated. Budgets include the complete returned text.

**Current status:** experimental. The new on-demand architecture passes local
correctness checks; its end-to-end token savings and task accuracy have not been
measured. The benchmarks below evaluate the earlier eager retrieval architecture.
MCP controls its own responses; a controlled adapter is required to replace other
tool observations before they enter history.

## Quick start

Requires Git and Python 3.12+. From the cloned repository:

```bash
python -m pip install -e ".[evidence]"
contextlens find --root . --query "refresh-token timeout" --encoding o200k_base
contextlens read --root . --handle h_REPLACE_WITH_RETURNED_HANDLE --encoding o200k_base
```

If you already know the location, read it directly:

```bash
contextlens read --root . --path src/auth.py --start-line 20 --end-line 60 --encoding o200k_base
```

To expose the three compact tools to an MCP-compatible agent:

```bash
contextlens mcp --root . --state .contextlens --encoding o200k_base
```

Discovery defaults to 1,200 returned-text tokens; reads default to 3,000. Use
`--budget` to change these limits. Exact token counting uses the selected encoding;
`estimate` is an explicitly approximate fallback. Installation currently includes
neural dependencies, although this workflow runs locally without loading a model
or requiring a GPU. Legacy retrieval and neural MCP tools remain available through
`retrieve` and `mcp --profile legacy`.

See the [architecture and controlled adapter guide](docs/evidence-architecture.md)
for integration, and the [legacy usage guide](docs/evidence-retrieval.md) for older tools.

## Benchmarks

### What we tested

These results are from the **previous eager retrieval workflow**, not the new compact tools or controlled adapter.

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

The current implementation passes **266 automated tests**, lint, and strict type checks.

```bash
python -m pip install -e ".[dev,evidence]"
python -m pytest -q
ruff check src tests
mypy
```

For more detail, see the [architecture](docs/evidence-architecture.md) and
[research notes](docs/research-context-efficiency-2026-09.md).

Released under the [MIT License](LICENSE).
