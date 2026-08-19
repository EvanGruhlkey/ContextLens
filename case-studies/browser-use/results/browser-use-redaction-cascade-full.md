## ContextLens — Agent Context Regression

**RESULT: PASS**

| Metric | Base | Candidate | Delta |
| --- | ---: | ---: | ---: |
| Success | 3/3 | 3/3 | +0.0% |
| Initial context | 9,616 | 579 | -94.0% |
| Provider input | 424,547 | 243,275 | -42.7% |
| Cached input | 375,296 | 212,736 | -43.3% |
| Uncached input | 49,251 | 30,539 | -38.0% |
| Output tokens | 2,428 | 2,358 | -2.9% |
| Reasoning tokens | — | — | — |
| Tool calls | 9.00 | 7.00 | -22.2% |
| Median latency | 141.62s | 121.57s | -14.2% |
| Estimated cost | — | — | — |

Candidate preserved measured task quality while reducing end-to-end provider input.
