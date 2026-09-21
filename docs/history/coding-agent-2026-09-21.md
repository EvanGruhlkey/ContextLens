# Paired coding-agent benchmark — 21 September 2026

This is the measured run that produced the current architecture. It predates
the two-layer rewrite, so its condition names are the ones used at the time:

| Report label | What it was |
| --- | --- |
| Baseline | raw tool output, no ContextLens |
| Jev Filter | Jev KEEP/DROP over tool-output chunks, no structural expansion. This is the ancestor of today's live-pruning layer. |
| ContextLens | Jev filtering plus deterministic Python AST expansion around whatever Jev kept. This path was retired; see [`experiments/structural_expansion/`](../../experiments/structural_expansion/). |

There was no transcript-compaction condition in this run.

Ten frozen real Python GitHub issues (Click, responses, Luigi, Powertools,
Flask, Babel), two of them from SWE-bench-Live. `gpt-5.6-luna` at
`reasoning.effort=low`, 20 turns, 300 s timeout, one trial. Hidden graders
calibrated 10/10. All 30 paired attempts completed. Jev scored through the
Vercel AI Gateway; coding-model calls stayed on OpenAI. Raw rows are in
[`coding-agent-2026-09-21.json`](coding-agent-2026-09-21.json).

Read it as one trial of ten tasks, not a statistical quality claim.

| Condition   | Verified Fixes | Coding Input Tokens | Uncached Input | Cached Input | Output Tokens | Total Coding Tokens | Raw Tool Output | Injected Tool Output | Tokens Removed | Agent Turns | Recoveries | Jev Input |
| ----------- | -------------: | ------------------: | -------------: | -----------: | ------------: | ------------------: | --------------: | -------------------: | -------------: | ----------: | ---------: | --------: |
| Baseline    | 5 | 583,459 | 67,718 | 515,741 | 14,975 | 598,434 | 46,950 | 46,950 | 0 | 136 | 0 | 0 |
| Jev Filter  | 7 | 379,716 | 57,359 | 322,357 | 16,137 | 395,853 | 48,276 | 26,920 | 21,356 | 134 | 0 | 136,505 |
| ContextLens | 5 | 1,105,615 | 151,696 | 953,919 | 14,989 | 1,120,604 | 138,931 | 119,054 | 19,877 | 141 | 0 | 124,519 |

| Metric                      | Jev Filter vs Baseline | ContextLens vs Baseline |
| --------------------------- | ---------------------: | ----------------------: |
| Coding-model input tokens   | -203,743 (-34.9%) | +522,156 (+89.5%) |
| Uncached coding-model input | -10,359 (-15.3%) | +83,978 (+124.0%) |
| Injected tool output        | -20,030 (-42.7%) | +72,104 (+153.6%) |
| Agent turns                 | -2 (-1.5%) | +5 (+3.7%) |
| Verified fixes              | +2 | +0 |

| Task      | Baseline Pass | ContextLens Pass | Baseline Input | ContextLens Input | Tokens Saved | Input Change |
| --------- | ------------- | ---------------- | -------------: | ----------------: | -----------: | -----------: |
| aws-powertools-eventbridge-replay | ✅ | ✅ | 79,418 | 29,772 | 49,646 | +62.5% |
| aws-powertools-query-merge | ❌ | ❌ | 125,593 | 79,077 | 46,516 | +37.0% |
| click-empty-default | ❌ | ❌ | 61,088 | 34,485 | 26,603 | +43.5% |
| click-short-help | ❌ | ❌ | 27,291 | 490,067 | -462,776 | -1695.7% |
| pallets-flask-trusted-hosts | ❌ | ❌ | 79,769 | 73,710 | 6,059 | +7.6% |
| python-babel-parse-time | ✅ | ✅ | 37,987 | 40,720 | -2,733 | -7.2% |
| responses-blank-query | ✅ | ✅ | 24,891 | 22,721 | 2,170 | +8.7% |
| responses-query-mutation | ✅ | ✅ | 24,342 | 24,796 | -454 | -1.9% |
| spotify-luigi-bool-default | ✅ | ✅ | 41,709 | 45,746 | -4,037 | -9.7% |
| spotify-luigi-run-arguments | ❌ | ❌ | 81,371 | 264,521 | -183,150 | -225.1% |

Verified fixes: Baseline 5/10, Jev Filter 7/10, ContextLens 5/10.

ContextLens used 522,156 more coding-model input tokens (+89.5%).
Uncached coding-model input changed by 83,978 (+124.0%).
Tool-output tokens prevented from entering model context: -72,104 (injected 119,054 vs baseline 46,950).
Agent turns increased by 5 (+3.7%).
Recoveries: 0 calls restoring 0 tokens.
Jev usage is separate from the coding model: 124,519 input / 21,914 output tokens, cost 0.000000.
