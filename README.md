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
measured. The small benchmarks below measure evidence delivery only.
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

## Small local benchmarks

Five cases, three runs each: **all required evidence checks passed**. Three are
synthetic fixtures; two are named functions in this repository. No model, GPU,
network calls or subscription capacity were used.

| Case | Full read tokens | Find + read tokens | Less returned text | Warm median |
| --- | ---: | ---: | ---: | ---: |
| Timeout function (fixture) | 3,645 | 88 | 97.6% | 0.25s |
| Large-class method (fixture) | 1,247 | 117 | 90.6% | 0.25s |
| Helper chain (fixture) | 3,651 | 111 | 97.0% | 0.26s |
| ContextLens interval helper | 2,884 | 167 | 94.2% | 2.44s |
| ContextLens scope analysis | 3,667 | 255 | 93.0% | 2.47s |

Tokens use `o200k_base` and include the complete returned text: locations, source,
support and notices. The comparison is a full-file read versus discovery plus a
grouped read. Warm latency covers discovery and reading after the first run.
These cases use known symbol names; they do not test broad task interpretation.

**This measures smaller evidence delivery, not total coding-agent token savings
or fix accuracy.** Source-anchor checks do not establish that an agent will make
a correct patch. Runtime is machine-dependent; repository discovery still takes
about 2.5 seconds warm on this Windows machine. Handle token counts can vary
slightly across runs. Old published benchmark results have been removed.

[Raw results](benchmarks/results/compact.json) · [Methods and reproduction](benchmarks/README.md)

## Development

The current implementation passes **267 automated tests**, lint, and strict type checks.

```bash
python -m pip install -e ".[dev,evidence]"
python -m pytest -q
ruff check src tests
mypy
```

For more detail, see the [architecture](docs/evidence-architecture.md) and
[research notes](docs/research-context-efficiency-2026-09.md).

Released under the [MIT License](LICENSE).
