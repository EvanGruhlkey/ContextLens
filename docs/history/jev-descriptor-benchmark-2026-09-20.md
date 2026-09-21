# Descriptor-first Jev selection benchmark — September 20, 2026

## Decision

Descriptor-first selection becomes the default repository context strategy. It
retained all required evidence in the five fixed development cases while reducing
complete Jev decision input by 28.6% relative to the full-source strategy in the
same paired run.

The full-source strategy remains available as a frozen comparison path. This
component result does not establish coding-task success or complete trajectory
savings.

## Paired result

| Metric | Full source | Descriptor first | Change |
| --- | ---: | ---: | ---: |
| Evidence checks | 5/5 | 5/5 | No change |
| Jev input tokens | 40,446 | 28,875 | 28.6% lower |
| Jev output tokens | 1,560 | 2,260 | 44.9% higher |
| Jev input + output | 42,006 | 31,135 | 25.9% lower |
| Exact context delivered | 3,787 | 3,519 | 7.1% lower |
| Mean decision latency | 452.8 ms | 806.6 ms | 78.1% higher |
| Reported cost | $0 | $0 | Promotional pricing |

The descriptor-first condition used 17,924 tokens for broad descriptor ranking
and 10,951 tokens for exact-source evaluation of at most eight shortlisted units.
No required anchor was missing and no fixture noise anchor was returned.

## What changed

Stage one sends bounded descriptors containing location, symbol, kind, signature,
role, local score, bindings, and relationships. Complete source bodies are absent.
A shared selection policy replaces repeated per-candidate rubric text.

Stage two sends exact source only for the eight highest-ranked descriptors. The
existing probability validation, source freshness checks, structural support,
response budgets, content-addressed handles, and deferred recovery remain in
force.

## Failed first attempt

The first two-stage implementation repeated the complete relevance rubric for
every descriptor. It preserved 5/5 evidence checks but increased decision input
by 18.5%. That measured failure is preserved in the initial comparison artifact.
Moving the rubric into shared state produced the successful result above.

## Limits

These are three synthetic fixtures and two known ContextLens tasks. They measure
evidence anchors, provider usage, returned context, and latency. They do not run a
coding model, edit files, execute task tests, or measure patch correctness. The
two-stage strategy makes an additional provider call, and its latency was higher.
End-to-end trajectory evaluation remains necessary before claiming product-level
token savings.

## Artifacts

- [Corrected paired run](../benchmarks/results/jev-selection-comparison-compact-questions-2026-09-20.json)
- [Initial repeated-rubric run](../benchmarks/results/jev-selection-comparison-2026-09-20.json)
- [Historical full-source benchmark](../benchmarks/results/jev-selection-2026-09-20.json)
