# ContextLens

**ContextLens uses Jev to decide which repository code a coding agent needs for a
task.** Jev runs through Vercel AI Gateway, evaluates compact local candidates,
and returns probabilities that ContextLens turns into exact, recoverable source.

For example, a task like “fix the refresh-token timeout” may only need a few
functions and their dependencies. ContextLens finds that code, records where it
came from, and lets the agent recover more of the original source when needed.
It provides context tools for an agent; it does not generate the fix itself.

## How it works

1. **Build candidates locally:** structural search produces compact functions and
   code regions without sending whole files.
2. **Let Jev decide:** one typed evaluation scores every candidate against the
   task and optional focus.
3. **Return exact evidence:** selected handles resolve to original source plus
   statically identified support, with a complete returned-text token budget.
4. **Recover safely:** source stays available through handles, and freshness
   checks prevent changed code from being presented as current.

Python methods and nested functions are indexed individually. Optional JavaScript
and TypeScript parsing provides similar granularity. Support resolution is bounded
and static; unresolved dependencies are reported. Oversized evidence groups are
refused rather than silently truncated. Budgets include the complete returned text.

**Current status:** experimental. A [whole-agent pilot](benchmarks/results/goal-e2e-2026-09-18/README.md)
completed nine matched comparisons: ContextLens used **24.12% more input**,
with **7/9 correct fixes in both conditions** and one paired quality regression.
This small development sample does not establish accuracy preservation or token savings.
The small benchmarks below measure evidence delivery only.
MCP controls its own responses; a controlled adapter is required to replace other
tool observations before they enter history.

## Quick start

Requires Git and Python 3.12+. From the cloned repository:

```powershell
python -m pip install -e .
$env:AI_GATEWAY_API_KEY = "your-vercel-ai-gateway-key"
contextlens select --root . --task "fix the refresh-token timeout"
```

If you already know the location, read it directly:

```bash
contextlens read --root . --path src/auth.py --start-line 20 --end-line 60 --encoding o200k_base
```

The selection response includes exact source and deferred handles for evidence
that did not fit the budget. Read or expand those handles when the task needs it.

To expose the Jev context tools to an MCP-compatible agent:

```bash
contextlens mcp --root . --state .contextlens --encoding o200k_base
```

Selection defaults to 3,000 returned-text tokens and 12 local candidates. Use
`--budget`, `--limit`, and `--focus` to tune a request. The default install contains
the local parsers and tokenizer used by this workflow; the older neural stack is
available with `.[neural]`. Legacy retrieval and MCP tools remain available through
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

The implementation is checked with automated tests, lint, and strict type checks.

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check src tests
mypy
```

For more detail, see the [architecture](docs/evidence-architecture.md) and
[research notes](docs/research-context-efficiency-2026-09.md).

Released under the [MIT License](LICENSE).
