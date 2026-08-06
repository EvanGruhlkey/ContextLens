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

ContextLens is a working pre-alpha library and CLI. Complete run/step recording,
structured context, normalized SQLite persistence, passive profiling, explicit
remove/summarize/lazy-load/scope mutations, isolated replay, deterministic
paired planning, coding-task evaluation, adaptive search, effect estimation,
policy export, and multi-format reports are implemented. See
[docs/architecture.md](docs/architecture.md),
[docs/mvp-workflow.md](docs/mvp-workflow.md),
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

> **Planner benchmark only. This does not measure end-to-end LLM task performance or production token savings.**

See [benchmarks/README.md](benchmarks/README.md).

The primary end-to-end evaluation launches fresh ephemeral Codex workers on
real public repositories at pinned pre-fix commits. It records the baseline,
passes that trace through the production profiler, adaptive replay, paired
analysis, and optimizer, then compares fresh full-context, ContextLens, and
matched-random runs using mechanical tests. Synthetic tasks are retained only
as unit fixtures. See [evals/README.md](evals/README.md) for the one-case and
six-case smoke commands.

### Real-repository LLM pilot

The fixture result below was followed by a commit-based pilot on three real
repositories from SWE-bench Verified. Each case used the repository's exact
pre-fix commit, the public issue as the task, a bounded retrieval bundle, a
fresh ephemeral Codex process and workspace for every invocation, and an
independent regression check. The solution patch was never provided to the
agent. The full repository remained available through shell tools under both
policies.

These are provider-reported input tokens from one fresh final-policy trial per
case. **Full** is the full retrieved-context benchmark and **After** is the
optimizer-produced ContextLens policy.

| Repository / SWE-bench case | Checkout size | Full | After | Delta | Change | Full result | After result |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Requests `psf__requests-5414` | 97 files | 448,151 | 186,091 | -262,060 | -58.5% | Pass | Pass |
| pytest `pytest-dev__pytest-10051` | 570 files | 229,598 | 279,777 | +50,179 | +21.9% | Pass | Pass |
| Sphinx `sphinx-doc__sphinx-10323` | 1,591 files | 319,805 | 272,196 | -47,609 | -14.9% | Fail | Fail |
| **Total / overall** | **2,258 files** | **997,554** | **738,064** | **-259,490** | **-26.0%** | **2/3 pass** | **2/3 pass** |

ContextLens reduced total input tokens while preserving the same aggregate
success count, but the result is not uniformly positive: it increased tokens
on pytest, and neither policy solved the Sphinx issue. Mean latency also rose
from 75.72 seconds to 77.48 seconds (+2.3%). With only one final trial per case,
this is a pilot rather than statistically reliable evidence of savings.

The run used ChatGPT-authenticated Codex CLI 0.146.0 with `gpt-5.6-luna` at low
reasoning. It performed 30 validated model invocations across the three cases
(10 per repository) with no retries or conversation reuse. Docker was not
available, so grading ran natively on Windows; these are not official
containerized SWE-bench scores. Artifacts are in
`evals/artifacts/real-20260806T030954Z-1b8f411f`,
`evals/artifacts/real-20260806T033419Z-665ca7be`, and the short-path pytest run
`C:\contextlens-eval-artifacts\real-20260806T034931Z-6cda1996`.

### Held-out LLM evaluation results

The validated held-out run used 20 cases, five final policies, and three fresh
trials per policy. In total it launched 520 isolated Codex workers with 520
unique conversations and workspaces. The provider was ChatGPT-authenticated
Codex CLI 0.146.0 using `gpt-5.6-luna` at low reasoning.

**Headline:** ContextLens matched full-context task quality, but did not reduce
actual provider-reported tokens or latency in this evaluation. It removed an
estimated 18.4% of fixture context, yet used 1,340 more input tokens and took
0.66 seconds longer per final task on average. This is a negative result for
production token savings on this suite.

| Policy | Success | Mean score | Input tokens | Cached tokens | Output tokens | Latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full context | 100% | 0.875 | 60,326 | 50,513 | 472 | 35.00 s |
| **ContextLens** | **100%** | **0.875** | **61,666** | **52,437** | **498** | **35.65 s** |
| Token-only pruning | 100% | 0.875 | 59,385 | 50,193 | 478 | 35.72 s |
| Matched random pruning | 100% | 0.875 | 59,528 | 49,779 | 485 | 35.90 s |
| Oracle context | 100% | 0.875 | 57,513 | 48,090 | 462 | 34.90 s |

#### Tokens by held-out test

These are provider-reported input tokens, averaged across the three fresh final
trials for each policy. **Full** is the full-context benchmark and **After** is
the optimizer-produced ContextLens policy. A negative delta means ContextLens
used fewer tokens.

| Test | Category | Task | Full | After | Delta | Change |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `normalize-identifier` | Bug fix | Fix identifier normalization | 77,663 | 74,095 | -3,569 | -4.6% |
| `retry-delay` | Bug fix | Repair retry-delay behavior | 80,250 | 74,757 | -5,493 | -6.8% |
| `feature-flag-parser` | Bug fix | Correct feature-flag parsing | 69,177 | 79,252 | +10,074 | +14.6% |
| `header-merge` | Bug fix | Fix header merging | 66,544 | 103,098 | +36,554 | +54.9% |
| `slugify` | Feature | Implement URL slugification | 70,451 | 70,715 | +264 | +0.4% |
| `bounded-page` | Feature | Implement bounded pagination | 75,043 | 70,492 | -4,551 | -6.1% |
| `redact-token` | Feature | Implement token redaction | 70,437 | 75,440 | +5,003 | +7.1% |
| `select-timeout` | Feature | Implement timeout selection | 90,361 | 90,117 | -244 | -0.3% |
| `api-config` | Configuration | Update production API config | 59,556 | 59,389 | -167 | -0.3% |
| `worker-config` | Configuration | Apply worker capacity decision | 65,550 | 64,351 | -1,199 | -1.8% |
| `database-config` | Configuration | Update database connection policy | 59,860 | 64,280 | +4,420 | +7.4% |
| `rollout-config` | Configuration | Apply approved feature rollout | 64,835 | 63,739 | -1,095 | -1.7% |
| `payments-incident` | Incident | Record payments mitigation | 52,826 | 48,482 | -4,345 | -8.2% |
| `queue-incident` | Incident | Record queue recovery action | 69,654 | 62,902 | -6,751 | -9.7% |
| `cache-incident` | Incident | Record cache mitigation | 59,531 | 54,093 | -5,438 | -9.1% |
| `tls-incident` | Incident | Record TLS remediation | 53,117 | 52,598 | -519 | -1.0% |
| `serialization-owner` | Codebase question | Identify serialization owner | 34,678 | 34,555 | -123 | -0.4% |
| `timeout-key` | Codebase question | Identify canonical timeout key | 25,897 | 30,179 | +4,282 | +16.5% |
| `compatibility-version` | Codebase question | Identify protocol compatibility floor | 30,392 | 34,719 | +4,328 | +14.2% |
| `release-command` | Codebase question | Identify release-validation command | 30,689 | 26,062 | -4,627 | -15.1% |

ContextLens used fewer tokens on 13 of 20 tests and more on 7. The large
regressions on `header-merge`, `feature-flag-parser`, and two codebase questions
outweighed the savings elsewhere, producing the overall increase of 1,340
input tokens per task, or 2.2% above full context.

The paired full-context-minus-ContextLens quality effect was `0.000`, with a
95% bootstrap interval of `[0.000, 0.000]` across 60 paired observations and
20 tasks. ContextLens recorded 0 wins, 60 ties, and 0 losses. All final-policy
tasks passed mechanical verification; two adaptive baseline workers timed out
and were retained as explicit failed invocation records. There were no retries
and no LLM judge calls.

The result should be interpreted cautiously: final scores saturated at the
same value for every policy, cases were small fixtures, and provider token
usage includes Codex system prompts, caching, reasoning, and tool turns rather
than only the supplied `ContextSource` text. USD cost and break-even could not
be calculated because the authenticated provider did not expose charges.

Validated local artifacts are under
`evals/artifacts/heldout-20260805T024211Z-40421c5b/`, including
`aggregate.json`, `invocations.jsonl`, per-case traces and reports,
`manifest.json`, and `checksums.json`. See [evals/README.md](evals/README.md)
for the exact execution and validation commands.

## Quick start

The CLI workflows are available:

```bash
contextlens record --output traces/task-001.jsonl -- your-agent-command
contextlens scan traces/task-001.jsonl
contextlens optimize experiment.json --format json --output runs/latest.json
contextlens report runs/latest.json --format html --output runs/latest.html
contextlens policy runs/latest.json --format yaml --output runs/policy.yaml
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
