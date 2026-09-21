# Jev primary-first evidence benchmark — September 20, 2026

## Result

The Jev-first architecture passed all five fixed evidence checks. It retained every
required source anchor and returned none of the fixture noise anchors. Across the
five cases, exact source responses used 3,818 tokens versus 12,204 tokens for exact
full-file reads, a 68.7% reduction in context delivered to the coding agent.

Jev used 34,405 input tokens and 1,560 output tokens to make those decisions. Vercel
reported a total cost of `$0`. Mean gateway latency was 434.2 ms and the slowest
request was 546.8 ms.

| Case | Evidence | Full file | Selected source | Reduction |
| --- | --- | ---: | ---: | ---: |
| Function with constant support | Passed | 3,041 | 220 | 92.8% |
| Method with class support | Passed | 1,250 | 209 | 83.3% |
| Function with transitive support | Passed | 3,047 | 218 | 92.8% |
| Gateway response validation | Passed | 1,140 | 1,945 | -70.6% |
| Scope declaration analysis | Passed | 3,726 | 1,226 | 67.1% |

## Interpretation

This run validates the implemented control split: local typed discovery proposes
primary and support units, Jev selects relevant primary implementations, structural
support follows selected code within the response budget, and ContextLens returns
exact source with deferred recovery handles.

It does not establish lower whole-system token use or better patch accuracy. Jev's
34,405 decision-input tokens exceed both the 12,204-token full-file baseline and the
3,818 tokens delivered to the coding agent. One repository case also returned more
text than its full file because several individually relevant units plus recovery
metadata exceeded that small file. The next optimization target is therefore the
decision representation and candidate count, not another output compressor.

The cases are fixed development diagnostics: three synthetic fixtures and two
known ContextLens tasks. They do not measure a coding agent, held-out localization,
edits, tests, cache behavior, or task success. The raw report preserves per-case
provider usage, latency, evidence anchors, response size, and repository revision.

## Reproduction

```powershell
$env:AI_GATEWAY_API_KEY = "your-vercel-ai-gateway-key"
python -m benchmarks.jev_selection `
  --root . `
  --output benchmarks/results/jev-selection-2026-09-20.json
```

[Raw results](../benchmarks/results/jev-selection-2026-09-20.json)
