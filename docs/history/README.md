# History

Measured results and design notes from earlier ContextLens architectures. None
of it describes the current product. It is kept because the measurements are
real and because several of them are the reason the current architecture is as
small as it is.

The code these documents describe is gone from `src/`. Recover it from Git
history if you need it; retired code that is still worth reading lives under
[`experiments/`](../../experiments/).

## Measured runs

| Report | What it measured | Headline |
| --- | --- | --- |
| [`coding-agent-2026-09-21.md`](coding-agent-2026-09-21.md) | Baseline vs Jev filtering vs Jev filtering + AST expansion, ten frozen real issues | Jev filtering alone: **379,716** coding-model input tokens and 7/10 fixes. Baseline: **583,459** and 5/10. AST expansion: **1,105,615** and 5/10 |
| [`controller-trajectory-benchmark-2026-09-20.md`](controller-trajectory-benchmark-2026-09-20.md) | A `context_next` loop where Jev chose the agent's next capability | 2/3 verified fixes, one timeout, **+156.21%** complete input on finished pairs. Adding a decision point cost input and turns |
| [`controller-loop-benchmark-2026-09-20.md`](controller-loop-benchmark-2026-09-20.md) | The bounded next-action controller in a loop | Retired with the controller |
| [`action-selection-benchmark-2026-09-20.md`](action-selection-benchmark-2026-09-20.md) | Jev picking one bounded action from a candidate list | Retired with the controller |
| [`jev-benchmark-2026-09-20.md`](jev-benchmark-2026-09-20.md) | Jev over repository evidence candidates | Retired with the evidence index |
| [`jev-descriptor-benchmark-2026-09-20.md`](jev-descriptor-benchmark-2026-09-20.md) | Compact descriptors vs full payloads in Jev requests | The lesson that descriptors are enough survives, in `compaction.py` |
| [`production-savings.md`](production-savings.md) | Static context-reduction measurements | Predates the tool-output framing |
| [`results/`](results/) | Raw JSON for the reports above | |
| [`case-studies/`](case-studies/) | Static context reports on pinned public repository changes (browser-use, Infisical, Langfuse) | Runner scripts removed; the recorded results and their invalid-run notes are kept |

## Design notes

[`evidence-architecture.md`](evidence-architecture.md),
[`evidence-retrieval.md`](evidence-retrieval.md),
[`adaptive-search.md`](adaptive-search.md),
[`context-optimization.md`](context-optimization.md),
[`legacy-repository-context.md`](legacy-repository-context.md),
[`evaluation-and-analysis.md`](evaluation-and-analysis.md),
[`contextlens-revision-plan.md`](contextlens-revision-plan.md),
[`research-context-efficiency-2026-09.md`](research-context-efficiency-2026-09.md),
[`legacy-roadmap.md`](legacy-roadmap.md), and [`adr/`](adr/) describe a
repository-evidence index, an adaptive search policy, a trace format with
replay workers, SQLite storage, and a context-optimization solver. All of that
was removed.
