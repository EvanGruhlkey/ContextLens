# ContextLens real-world paired experiments

All performance tables below use pinned historical commits, hidden mechanical
graders, fresh isolated agent runs, and matched base/candidate conditions.
Infrastructure-invalid attempts are excluded and retained in
[`INVALID_RUNS.md`](INVALID_RUNS.md).

## Browser Use — redaction cascade

- Buggy commit: `af2d7a593980b20ade90ad13a128d9dd904ad26f`
- Upstream fixed commit: `2e85f567dac6cb102cf79c184f7283f651746982`
- Agent: Codex CLI `0.146.0`, `gpt-5.6-terra`, low reasoning
- Hidden grader: six mechanical tests

### Task correctness

| Trial | Order | Base | Candidate | Mechanical score |
| ---: | --- | :---: | :---: | ---: |
| 1 | Base → Candidate | PASS | PASS | 0.861 → 0.861 |
| 2 | Candidate → Base | PASS | PASS | 0.861 → 0.861 |
| 3 | Base → Candidate | PASS | PASS | 0.861 → 0.861 |

Both variants passed all three trials. There were zero catastrophic regressions
and zero infrastructure-invalid executions.

### Paired economics

| Trial | Base provider input | Candidate provider input | Paired delta | Base uncached | Candidate uncached | Uncached delta |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 351,935 | 319,059 | -32,876 (-9.3%) | 52,415 | 63,571 | +11,156 (+21.3%) |
| 2 | 424,547 | 188,152 | -236,395 (-55.7%) | 49,251 | 28,152 | -21,099 (-42.8%) |
| 3 | 600,819 | 243,275 | -357,544 (-59.5%) | 44,531 | 30,539 | -13,992 (-31.4%) |
| **Median paired delta** | — | — | **-236,395 (-55.7%)** | — | — | **-13,992 (-31.4%)** |

| Metric | Base median | Candidate median | Difference of medians |
| --- | ---: | ---: | ---: |
| Provider input | 424,547 | 243,275 | -181,272 (-42.7%) |
| Uncached input | 49,251 | 30,539 | -18,712 (-37.9%) |
| Output | 2,428 | 2,358 | -70 (-2.9%) |
| Tool calls | 9 | 7 | -2 (-22.2%) |
| Latency | 141.6 s | 121.6 s | -20.1 s (-14.2%) |

The median paired latency delta was **-15.9 seconds (-11.2% relative to its
paired base)**. The median paired tool-call delta was **-2**.

Effective initial context was 9,616 base tokens versus 579 candidate tokens.
This static reduction is reported after correctness and end-to-end economics.

### Verdict

**PASS** — in this three-trial paired case study, candidate context preserved
measured task quality while reducing end-to-end provider input. This is one
historical task on one repository, not a universal performance claim.

Raw evidence:

- [`browser-use-redaction-cascade-full.json`](browser-use/results/browser-use-redaction-cascade-full.json)
- [`browser-use-redaction-cascade-full.md`](browser-use/results/browser-use-redaction-cascade-full.md)
- [`browser-use-redaction-cascade-preregistered-v1.json`](browser-use/results/browser-use-redaction-cascade-preregistered-v1.json)
- [`browser-use-redaction-cascade-validation.json`](browser-use/results/browser-use-redaction-cascade-validation.json)
