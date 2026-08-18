# ContextLens

**Test your AI agent's context like you test your code.**

ContextLens measures whether changes to `AGENTS.md`, `CLAUDE.md`, skills, MCP
tools, and other repository context actually improve coding-agent performance
— or just consume more tokens.

Static analysis finds obvious context bloat immediately. Controlled A/B
replays verify whether changing that context preserves task quality before you
ship it.

```bash
# Value immediately: no API key, model call, config, or instrumentation
contextlens scan
contextlens scan --target packages/api/src/auth.ts --provider codex
contextlens diff --base origin/main

# Bootstrap and run matched base/candidate task trials
contextlens init
contextlens verify .contextlens/evals.json --base origin/main
```

ContextLens is pre-alpha. The static workflow is local and deterministic. The
verified workflow is deliberately opt-in because it runs real agent tasks and
can incur provider cost.

## Why this exists

Teams increasingly commit agent configuration alongside code, but rarely test
it with the same discipline:

- Does a new instruction improve task success?
- Does it reduce exploration, or make the agent reread more files?
- Did a smaller prompt destroy a reusable cached prefix?
- Does guidance help one task while regressing another?
- Is a nested rule repeating root guidance?
- Does a path in an instruction still exist?

A smaller initial prompt is not automatically a cheaper agent run. An agent
can compensate by searching more, taking more turns, losing cache hits, or
triggering compaction differently. ContextLens therefore keeps two measurements
separate:

1. **Repository footprint** — all recognized context configuration in Git.
2. **Effective task context** — context whose documented scope matches targets.
3. **Agent economics** — what the provider processed across the complete run,
   including cached/uncached input, output, reasoning, pricing, and latency.

ContextLens does not assume more context is good or less context is good. It
measures the effect on your repository, tasks, and agent.

## The 60-second workflow

Install from a checkout and scan any repository:

```bash
python -m pip install -e .
cd /path/to/your/repository
contextlens scan
contextlens init
```

```text
ContextLens

Repository context footprint          Tokens
-------------------------------------------------
AGENTS.md                              3,420
packages/api/AGENTS.md                 1,180
CLAUDE.md                              2,740
-------------------------------------------------
Total                                  7,340

Findings (observed / static — NOT VERIFIED)
- 620 estimated tokens repeated across AGENTS.md and packages/api/AGENTS.md
- AGENTS.md references missing path `src/legacy/client.py`

Static findings do not establish performance impact or safe removal.
Run `contextlens verify` to measure whether a context change helps.
```

Token counts are deterministic UTF-8 byte estimates unless an adapter provides
recorded provider counts. Static output never claims causal performance impact.

For a complete no-credential demo using a temporary Git repository and a
deterministic fixture agent:

```bash
python examples/context-regression/run_demo.py
```

The fixture's usage numbers demonstrate the workflow only; they are not a
benchmark or production-savings claim.

`init` detects common Python, Node, Rust, and Go checks and writes a minimal
`.contextlens/evals.json`. If it cannot find an agent or meaningful mechanical
check, it writes explicit TODO placeholders and says the suite is not runnable
instead of manufacturing a task.

## Repository footprint versus effective context

Plain `scan` inventories every recognized context file in the repository. It
does **not** claim every file is injected for every task. Resolve context for
one or more concrete targets when task scope matters:

```bash
contextlens scan --target backend/api/user.py --provider codex
contextlens scan --target frontend/app.tsx --provider copilot
contextlens scan --target src/a.py --target src/b.py --format json
```

```text
Effective Agent Context

Target: backend/api/user.py
Resolver: codex

Source                                Tokens  Scope
---------------------------------------------------------------
AGENTS.md                              2,840  approximated
backend/AGENTS.md                      1,120  approximated
---------------------------------------------------------------
Effective context                     3,960
```

Scoped Copilot instructions and metadata-scoped Cursor rules use deterministic
path resolution where documented. Codex target resolution assumes the target's
parent is the run directory; CLAUDE.md and relevance-based Cursor behavior are
also labeled `approximated`. Missing or moved targets are resolved lexically
and reported as such.

## `contextlens init`

```bash
contextlens init
contextlens init --output .contextlens/evals.json
```

The generated suite is a starting point, not automatically trustworthy ground
truth. Replace its broad repository-check task with small historical bugs or
explicit coding tasks before publishing causal claims.

## `contextlens scan`

`scan` discovers common repository context without a model call:

- root and nested `AGENTS.md`
- root and nested `CLAUDE.md`
- `.github/copilot-instructions.md` and scoped Copilot instructions
- `.cursor/rules/**`
- repository skill definitions such as `SKILL.md`
- recognized MCP configuration and static tool-schema files

It reports estimated footprint by source, exact and conservative near
duplicates, nested-scope duplication, explicit path references that no longer
exist, potential modal conflicts, narrow guidance living at root scope, and
tool/schema footprint.

```bash
contextlens scan
contextlens scan packages/api --format json --output context-scan.json
contextlens scan --format markdown --output context-scan.md
```

Every result is labeled `observed / static — NOT VERIFIED`. A duplicate can
still be useful; a stale-looking sentence can still encode important intent.

## `contextlens diff`

`diff` treats agent context as Git-versioned configuration. It compares the
worktree with an explicit ref, or chooses the merge-base with `origin/main`,
`main`, `origin/master`, or `master`:

```bash
contextlens diff --base origin/main
contextlens diff --base HEAD^ --format json --output context-diff.json
```

The report shows per-source base/candidate tokens, total footprint change,
duplicate-token change, and stale-reference change. It reads the base directly
from the immutable Git tree; it does not check out or modify files.

## `contextlens verify`

`verify` answers the product's core question: **did this context change help?**

It discovers base context from Git and candidate context from the worktree,
then runs matched tasks in fresh isolated workspace copies. Task, repository
state, agent command, model settings, tools, evaluator, and trial policy stay
fixed. Only the supplied context version changes. Trial order alternates to
reduce simple ordering bias.

```bash
contextlens verify .contextlens/evals.json --base origin/main
```

A small suite uses existing mechanical checks or expected output:

```json
{
  "trials": 3,
  "quality_tolerance": 0,
  "economics_tolerance": 0.02,
  "require_provider_usage": true,
  "agent": {
    "type": "subprocess",
    "command": ["python", "tools/contextlens_agent.py"],
    "adapter_id": "our-agent-v1",
    "provider": "provider",
    "model": "model",
    "temperature": 0
  },
  "tasks": [
    {
      "id": "parser-regression",
      "instruction": "Fix the parser regression.",
      "workspace": ".",
      "checks": [["python", "-m", "pytest", "tests/test_parser.py", "-q"]],
      "allowed_files": ["src/parser.py"]
    }
  ]
}
```

The subprocess receives `CONTEXTLENS_REQUEST`, the path to a JSON request, and
may write provider usage and behavior to `CONTEXTLENS_RESULT`. A built-in Codex
CLI adapter is also available with `"type": "codex"`. See
[the CLI contract](docs/cli.md).

Verification reports:

- quality: mechanical success, pass rate, score, and catastrophic failures;
- economics: initial context, provider input, cached/uncached input, cache
  writes, output, reasoning, and explicit historical pricing when configured;
- behavior: inference turns, tool calls, files read, searches, commands,
  exploration breadth, and retries when the adapter exposes them;
- performance: total, model, and tool latency when available.

Verdicts are `PASS`, `WARN`, `CONTEXT REGRESSION`, or `INCONCLUSIVE`. Any
observed candidate failure that passed under base context fails closed. A
smaller initial context that increases provider or uncached input without a
quality improvement is a context regression, not a token-saving success.

## `contextlens minimize`

`minimize` is the advanced token-saving workflow. Static signals generate a
bounded candidate; the configured task suite verifies the combined change:

```bash
contextlens minimize AGENTS.md packages/api/AGENTS.md \
  --config .contextlens/evals.json \
  --patch-output .contextlens/patches/context-minimized.diff
```

The first conservative implementation mutates exact repeated instructions.
The underlying mutation engine also retains remove, summarize, lazy-load, and
scope operations for programmatic and adaptive experiments. A suggested patch
is written only after a `PASS` verdict with a smaller footprint. Source files
are never modified. `WARN`, `INCONCLUSIVE`, any quality regression, or an
economics regression produces no patch.

Without `--config`, the command lists static candidates as **NOT VERIFIED** and
does not recommend them.

## GitHub Action and CI

Static CI is fast, deterministic, credential-free, and suitable for every PR:

```yaml
name: Agent context
on:
  pull_request:
    paths:
      - "**/AGENTS.md"
      - "**/CLAUDE.md"
      - ".cursor/rules/**"
      - "**/SKILL.md"

permissions:
  contents: read

jobs:
  contextlens:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: EvanGruhlkey/ContextLens@main
        with:
          mode: static
          base: ${{ github.event.pull_request.base.sha }}
          max-context-increase: "0.25"
          max-duplicate-increase: "0"
```

Verified CI uses `mode: verified` and a checked-in `.contextlens/evals.json`.
It is optional because it incurs agent/provider work. Both modes write a GitHub
step summary and `.contextlens/ci-result.json`; threshold or verified-regression
failures exit with code `4`.

See [CI setup](docs/ci.md) and the
[static](examples/github-actions/static.yml) and
[verified](examples/github-actions/verified.yml) examples.

## How verification works

```text
Git base context ─────┐
                     ├─ matched task + isolated workspace ─ evaluator ─┐
Candidate worktree ──┘                                                  │
                                                                        ▼
                     quality + cache-aware economics + behavior + latency
                                                                        │
                                                                        ▼
                                             PASS / WARN / FAIL / INCONCLUSIVE
```

The existing ContextLens experimentation infrastructure remains the engine:
versioned context traces, isolated replay workers, explicit mutation records,
repeated paired trials, adaptive search, bootstrap uncertainty, mechanical
evaluators, normalized SQLite persistence, reports, policy generation, and the
deployment safety gate.

Mechanical evaluation is preferred: tests, builds, type checks, linters,
expected behavior, and deterministic assertions. A model judge is optional and
never silently substitutes for mechanical correctness.

Verification resolves repository instructions per task. When a task declares
`target_paths`, ContextLens independently computes the base and candidate
effective context using that task's `context_provider` (`codex`, `claude`,
`copilot`, `cursor`, or `portable`). Reports persist the targets, provider,
exact source paths, effective initial tokens, resolution mode, and scope
warnings. Tasks without targets retain the backward-compatible full-inventory
behavior and are labeled with an explicit warning.

## Why measure context?

There is no universal answer to whether more repository context helps coding
agents. Recent studies use different agents, repositories, tasks, guidance,
and evaluation protocols—and reach materially different conclusions.

| Study | Reported finding |
| --- | --- |
| [Gloaguen et al., *Evaluating AGENTS.md*](https://arxiv.org/abs/2602.11988) | Context files did not generally improve success in the studied settings and increased average inference cost by over 20%. |
| [Lulla et al., *On the Impact of AGENTS.md*](https://arxiv.org/abs/2601.20404) | Across 10 repositories and 124 PRs, AGENTS.md was associated with 28.64% lower median runtime and 16.58% lower median **output-token** consumption, with comparable completion behavior. |
| [Khatri, *Do Context Files Help Coding Agents?*](https://arxiv.org/abs/2607.27250) | Across Claude Code and Codex, 17 real tasks, and 288 evaluated runs, context strategy did not measurably change correctness within the study's bounds. |
| [Shepard and Albrecht, *Probe-and-Refine Tuning*](https://arxiv.org/abs/2606.20512) | Experimentally refined guidance reached a 33.0% mean resolve rate versus 28.3% for its static knowledge base and 25.5% without guidance in that setup. |
| [dos Santos et al., *Configuration Smells in AGENTS.md Files*](https://arxiv.org/abs/2606.15828) | In 100 popular repositories, configuration smells were widespread; Context Bloat appeared in 42% of analyzed files. |
| [Sam-Bodden, *What Context Does a Coding Agent Actually Need to Act?*](https://arxiv.org/abs/2607.09691) | With localization held fixed, compressed code context matched whole files at substantially fewer context tokens in the reported experiments. |

The contradiction is the motivation. ContextLens turns it into an engineering
question: measure your context change under your actual constraints.

## Why repository regression testing, not another runtime compactor?

Agent harnesses increasingly manage runtime context themselves. OpenAI's
[Codex agent-loop description](https://openai.com/index/unrolling-the-codex-agent-loop/)
explains exact-prefix prompt caching, cache-miss hazards, growing prompts, and
automatic compaction. Anthropic documents
[tool search, prompt caching, programmatic tool calling, and context editing](https://platform.claude.com/docs/en/agents-and-tools/tool-use/manage-tool-context),
including cache invalidation tradeoffs. VS Code describes
[deferred tool loading through tool search](https://code.visualstudio.com/blogs/2026/06/17/improving-token-efficiency-in-github-copilot).

ContextLens does not try to replace those runtime systems, a general tracing
backend, or an arbitrary eval platform. It specializes in the causal effect of
changing repository-owned agent context:

```text
context configuration
    + controlled A/B agent execution
    + end-to-end token economics
    + regression gating
    + verified minimization
```

## Current limitations

- Static token counts are estimates, not provider tokenizer counts.
- Effective-context resolution models documented path scope; it cannot promise
  byte-for-byte equality with every proprietary provider prompt.
- Discovery recognizes documented conventions; provider-specific formats can
  require an adapter.
- Directory-copy workspaces isolate file mutations, not the OS, network, or
  credentials. Use containerized workers for stronger isolation.
- Remote models are nondeterministic. Repeated trials expose variance but do
  not create exact reproducibility.
- Provider cost is reported only when the adapter supplies cost or the user
  provides a complete dated pricing snapshot.
- Cached, reasoning, model-latency, tool-latency, file-read, and search metrics
  remain unavailable when the agent does not expose them.
- Static findings generate candidates; they never prove that content is safe
  to remove.
- The included historical evaluation has both positive and negative cases. In
  the 20-case held-out fixture run, ContextLens preserved quality but increased
  provider input by 2.2%; this negative result is retained in
  [the evaluation documentation](evals/README.md).

## Real repository context changes

[`case-studies/cases.json`](case-studies/cases.json) pins seven public context
changes across six repositories, including VS Code adding `AGENTS.md`, MCP
Servers removing redundant `AGENTS.md`/`CLAUDE.md`, and focused changes in uv,
Rust, Codex, and Awesome Copilot. The checked-in VS Code reproduction reports:

```text
Repository footprint: 5,966 -> 6,034 estimated tokens (+68, +1.1%)
Changed source: AGENTS.md (added)
Evidence: observed/static — NOT VERIFIED
```

Reproduce it with:

```bash
python case-studies/run_static.py --case vscode-add-agents \
  --output case-studies/reports/vscode-add-agents.json
```

This is a real Git-history report, not a model-performance benchmark. The
corpus deliberately withholds `verified` status until realistic tasks,
mechanical graders, repeated matched trials, and raw usage evidence exist.

## Architecture for contributors

The public product layer is additive:

- `repository.py` — convention discovery, static findings, and Git comparison;
- `bootstrap.py` — repository/test/agent detection for `contextlens init`;
- `regression.py` — matched base/candidate orchestration and verdicts;
- `telemetry.py` — provider usage and explicit cache-aware pricing categories;
- `minimize.py` — static candidate generation plus fail-closed verification;
- `ci.py` — stable static and verified CI gates;
- `runtime.py` — deterministic application of already-verified policies.

The difficult original subsystems remain intact under `trace/`, `profiler/`,
`experiments/`, `evaluators/`, `analysis/`, `optimization/`, `storage/`, and
`reports/`. See [architecture](docs/architecture.md),
[migration notes](docs/migration.md), and [telemetry adapters](docs/adapters.md).

## Installation

ContextLens requires Python 3.11 or newer and has no required runtime
dependencies:

```bash
git clone https://github.com/EvanGruhlkey/ContextLens.git
cd ContextLens
python -m pip install -e .
contextlens --help
```

The `contextlens` PyPI project name was unclaimed when this release was
prepared, but similarly named and unrelated projects already exist. Until an
official package is published from this repository, install from Git or a
locally built wheel and verify the repository URL above.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check .
mypy
python -m build
```

The planner benchmark is a deterministic algorithm test, not an LLM
performance or savings benchmark. Real-agent claims must include tasks, sample
size, settings, evaluator, failures, uncertainty, and raw provider categories.

## Research and system references

- [OpenAI: Unrolling the Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)
- [Anthropic: Manage tool context](https://platform.claude.com/docs/en/agents-and-tools/tool-use/manage-tool-context)
- [Anthropic: Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- [VS Code: Improving token efficiency for GitHub Copilot](https://code.visualstudio.com/blogs/2026/06/17/improving-token-efficiency-in-github-copilot)
- [OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai)
- [Agent span conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)

## License

ContextLens is released under the [MIT License](LICENSE).
