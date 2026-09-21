| Condition   | Verified Fixes | Coding Input Tokens | Uncached Input | Cached Input | Output Tokens | Total Coding Tokens | Raw Tool Output | Injected Tool Output | Tokens Removed | Agent Turns | Recoveries | Jev Input |
| ----------- | -------------: | ------------------: | -------------: | -----------: | ------------: | ------------------: | --------------: | -------------------: | -------------: | ----------: | ---------: | --------: |
| Baseline    | 6 | 468,766 | 64,773 | 403,993 | 14,631 | 483,397 | 40,751 | 40,751 | 0 | 125 | 0 | 0 |
| Jev Filter  | 5 | 452,790 | 62,653 | 390,137 | 14,967 | 467,757 | 38,036 | 38,573 | 0 | 119 | 0 | 0 |
| ContextLens | 5 | 489,836 | 63,405 | 426,431 | 16,234 | 506,070 | 39,555 | 39,980 | 0 | 128 | 0 | 0 |

| Metric                      | Jev Filter vs Baseline | ContextLens vs Baseline |
| --------------------------- | ---------------------: | ----------------------: |
| Coding-model input tokens   | -15,976 (-3.4%) | +21,070 (+4.5%) |
| Uncached coding-model input | -2,120 (-3.3%) | -1,368 (-2.1%) |
| Injected tool output        | -2,178 (-5.3%) | -771 (-1.9%) |
| Agent turns                 | -6 (-4.8%) | +3 (+2.4%) |
| Verified fixes              | -1 | -1 |

| Task      | Baseline Pass | ContextLens Pass | Baseline Input | ContextLens Input | Tokens Saved | Input Change |
| --------- | ------------- | ---------------- | -------------: | ----------------: | -----------: | -----------: |
| aws-powertools-eventbridge-replay | ✅ | ✅ | 58,529 | 80,518 | -21,989 | -37.6% |
| aws-powertools-query-merge | ❌ | ❌ | 89,528 | 78,941 | 10,587 | +11.8% |
| click-empty-default | ❌ | ❌ | 53,261 | 48,392 | 4,869 | +9.1% |
| click-short-help | ❌ | ❌ | 25,816 | 36,642 | -10,826 | -41.9% |
| pallets-flask-trusted-hosts | ✅ | ❌ | 84,166 | 65,356 | 18,810 | +22.3% |
| python-babel-parse-time | ✅ | ✅ | 28,745 | 33,419 | -4,674 | -16.3% |
| responses-blank-query | ✅ | ✅ | 24,635 | 28,882 | -4,247 | -17.2% |
| responses-query-mutation | ✅ | ✅ | 22,915 | 19,320 | 3,595 | +15.7% |
| spotify-luigi-bool-default | ✅ | ✅ | 26,044 | 41,615 | -15,571 | -59.8% |
| spotify-luigi-run-arguments | ❌ | ❌ | 55,127 | 56,751 | -1,624 | -2.9% |

Jev did not score (`AI_GATEWAY_API_KEY` unset). Jev Filter and ContextLens failed open to passthrough, so tool-output tokens removed are 0. Condition totals differ because each attempt is an independent coding-agent trajectory, not because ContextLens filtered context.

Verified fixes: Baseline 6/10, Jev Filter 5/10, ContextLens 5/10.

ContextLens used 21,070 more coding-model input tokens (+4.5%).
Uncached coding-model input changed by -1,368 (-2.1%).
Injected tool-output tokens differed by -771 (injected 39,980 vs baseline 40,751); tokens removed by filtering were 0.
Agent turns increased by 3 (+2.4%).
Recoveries: 0 calls restoring 0 tokens.
Jev usage is separate from the coding model: 0 input / 0 output tokens, cost 0.000000.
Do not treat these trajectory deltas as ContextLens filter savings or as a measured quality regression from filtering.
