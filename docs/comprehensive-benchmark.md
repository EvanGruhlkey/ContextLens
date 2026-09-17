# Expanded repository benchmark

**Incomplete expanded evaluation:** 85/120 attempts across 10 historical bug-fix tasks in 5 real repositories. We allocated 3 repeats per task and condition, using `gpt-5.6-luna` with low reasoning effort.

| Policy | Passed / attempted | Total model tokens | Median time | Invalid | Timeouts |
| --- | --- | ---: | ---: | ---: | ---: |
| Normal tools | 19 / 21 | 5,466,699 | 76.6s | 0 | 0 |
| ContextLens full files | 18 / 20 | 11,273,242 | 88.5s | 0 | 0 |
| Lexical compression | 20 / 23 | 7,924,708 | 86.8s | 0 | 0 |
| Dependency compression | 18 / 21 | 7,618,593 | 79.6s | 1 | 0 |

Tokens include provider-reported input (including cached input) and output across the entire attempt, including failed fixes and extra reads. Unknown usage is never counted as zero. Time includes initial retrieval, agent execution and external verification; checkout/setup time is excluded.

Passes require the patch to pass the hidden task checks and the run to satisfy its tool protocol. An invalid run is not a pass; timeouts are also counted as unsuccessful attempts. Every unmodified checkout failed its checks before agent execution.
The evaluation stopped at the user's request. 2 in-progress attempts were canceled; their unknown usage is excluded from the totals. Conditions contain different numbers of finished tasks.
1 attempt failed the required successful evidence verification/read workflow; 1 of those patches passed the code checks. They remain invalid in the table and are excluded from matched comparisons; their reported tokens remain in the totals.

### Results by task

| Repository / task | Normal | Full files | Lexical | Dependency |
| --- | ---: | ---: | ---: | ---: |
| aws-powertools/powertools-lambda-python / `aws-powertools-eventbridge-replay` | 1/1 | 2/2 | 3/3 | 3/3 |
| aws-powertools/powertools-lambda-python / `aws-powertools-query-merge` | 2/2 | 1/2 | 3/3 | 2/2 |
| spotify/luigi / `spotify-luigi-bool-default` | 3/3 | 2/2 | 2/2 | 1/1 |
| spotify/luigi / `spotify-luigi-run-arguments` | 3/3 | 3/3 | 3/3 | 1/2 |
| microsoft/tslib / `microsoft-tslib-async-delegator` | 1/1 | 0/0 | 2/2 | 2/2 |
| microsoft/tslib / `microsoft-tslib-spread-array` | 1/1 | 2/2 | 2/2 | 2/2 |
| pallets/click / `click-empty-default` | 2/3 | 2/2 | 1/2 | 2/3 |
| pallets/click / `click-short-help` | 1/2 | 1/2 | 0/2 | 0/1 |
| getsentry/responses / `responses-blank-query` | 2/2 | 2/2 | 2/2 | 2/2 |
| getsentry/responses / `responses-query-mutation` | 3/3 | 3/3 | 2/2 | 3/3 |

Cells show passed / attempted runs; see the raw report for invalid-run statuses and per-task token usage.

### What this tells us

- **full:** 114.2% more tokens against normal tools over 16 complete matched pairs; 1 pass-to-fail regressions and 1 fail-to-pass improvements.
- **lexical:** 40.7% more tokens against normal tools over 16 complete matched pairs; 2 pass-to-fail regressions and 0 fail-to-pass improvements.
- **dependency:** 35.1% more tokens against normal tools over 14 complete matched pairs; 1 pass-to-fail regressions and 1 fail-to-pass improvements.

Full-file retrieval remains the default; compression requires explicit opt-in. These measurements do not establish quality preservation or universal savings.

This is a convenience sample of public historical fixes, not a contamination-free or randomly selected benchmark. Checks cover the bug and selected regressions, not complete project test suites. Tasks often name affected symbols; these results do not establish performance on vague reports, large feature work or unseen repositories. Full-file ContextLens seeds up to three ranked files under a 30,000-source-token budget; lexical and dependency policies use 3,000. ContextLens conditions must exercise live hash verification and range reads; the normal baseline has no ContextLens server or seed context. That mandatory step adds integration overhead, so the baseline comparison includes both retrieval choices and this workflow requirement. Two attempts run concurrently. No newly trained neural model is used.
This matrix evaluates deterministic retrieval and source-read integration. Optional neural pruning and long-conversation memory are separate capabilities whose end-to-end quality is not established here.

No paid API calls were initiated. Agent runs consume existing subscription capacity; dollar cost and dollar savings are unknown.

[Detailed results and uncertainty](comprehensive-benchmark.md) · [Raw measured report](../benchmarks/results/comprehensive.json) · [Reproduction instructions](../benchmarks/README.md)

Verifier calibration: undocumented formatting requirements were removed for Click help and Luigi error messages. All affected attempts were rechecked uniformly; original checks and scores are retained in the raw report. Agent prompts and production source were unchanged.
Offline regrading time is excluded from attempt timings; those include the original external verification step.

### Earlier neural runtime measurements

These archived free-Colab T4 runs used an earlier source snapshot and three files with three reads each. They measure returned-text size and runtime, rather than total agent tokens or bug-fix correctness.

| Runtime | Backend failures | Returned-text reduction | First read | Warm median |
| --- | ---: | ---: | ---: | ---: |
| Default (invalid run) | 4/9 | 25.62% | 45.99s | 2.56s |
| Experimental efficient SDPA | 0/9 | 75.45% | 17.69s | 2.83s |

The default exhausted GPU memory. The experimental variant's numerical equivalence is unverified; its smaller returned text does not establish agent-quality preservation. See the [runtime audit and raw reports](benchmark-audit.md).

## Paired uncertainty

- full versus normal: task-weighted success difference +0.0 percentage points; task-cluster bootstrap 95% interval [-16.7, +16.7] percentage points, from 16 valid matched pairs across 9 tasks.
- lexical versus normal: task-weighted success difference -15.0 percentage points; task-cluster bootstrap 95% interval [-35.0, +0.0] percentage points, from 16 valid matched pairs across 10 tasks.
- dependency versus normal: task-weighted success difference +0.0 percentage points; task-cluster bootstrap 95% interval [+0.0, +0.0] percentage points, from 14 valid matched pairs across 9 tasks.
- lexical versus full: task-weighted success difference -16.7 percentage points; task-cluster bootstrap 95% interval [-50.0, +11.1] percentage points, from 16 valid matched pairs across 9 tasks.
- dependency versus full: task-weighted success difference -5.6 percentage points; task-cluster bootstrap 95% interval [-33.3, +16.7] percentage points, from 15 valid matched pairs across 9 tasks.

Intervals use 2,000 task resamples with analysis seed 731.

Task-cluster bootstrap on a convenience sample; public tasks may be contaminated and tasks from the same repo remain correlated.

## Token and tool accounting

| Policy | Input | Cached input subset | Uncached input | Output | Live evidence calls | Expansion requests |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| normal | 5,418,503 | 4,667,648 | 750,855 | 48,196 | 0 | 0 |
| full | 11,222,523 | 10,290,176 | 932,347 | 50,719 | 56 | 0 |
| lexical | 7,867,442 | 7,055,616 | 811,826 | 57,266 | 66 | 1 |
| dependency | 7,572,054 | 6,684,416 | 887,638 | 46,539 | 64 | 1 |

Cached input is included in input, not added again. Tool-call counts include failed requests; expansion requests count calls to the snapshot expansion tool, not verified recovery successes. Provider token counts include repeated conversation context across model turns, not just unique source text.

Production source SHA-256: `1acf55cce24e081c284ca061c644e247337c49dfac2d289ca78deda39b08d6ba`.
