# Agent study status

Observed on Windows on 2026-08-18. Task selection and commits were locked before
candidate outcomes. Raw validation reports are checked in under each study's
`results/` directory.

| Task | Candidate edited-file tokens | Hidden grader |
| --- | ---: | --- |
| Browser Use redaction cascade | 9,616 → 579 (-94.0%) | buggy 1, fixed 0; ready |
| Browser Use zero compaction threshold | 9,616 → 579 (-94.0%) | buggy 1, fixed 0; ready |
| Infisical LDAP CN case | 1,742 → 1,566 (-10.1%) | buggy 1, fixed 0; ready |
| Infisical null connection message | 1,728 → 1,400 (-19.0%) | buggy 1, fixed 0; ready |
| Langfuse flattened OTEL parameters | 4,313 → 3,729 (-13.5%) | buggy 1, fixed 0; ready |
| Langfuse project-role entitlement | 6,860 → 5,860 (-14.6%) | setup passes; Windows Vitest path discovery blocks both revisions |

The token values above cover candidate-edited instruction files and use
ContextLens' byte-based estimate. They are not measured prompt usage, model
quality, latency, or dollar cost.

The paired runner was exercised with the pinned `@openai/codex@0.146.0` CLI and
ChatGPT authentication. Four Browser Use agent invocations completed. The first
two exposed cleanup/report-retention defects in the experiment harness; the
second paired attempt retained both raw provider traces, but both trials were
correctly classified as infrastructure-invalid because workspace cleanup and
the hidden grader environment failed. The retained descriptive measurements
were 636,733 base versus 240,683 candidate provider-input tokens and 136.7 s
versus 87.4 s latency. These are **not causal savings or performance results**:
the pair is excluded from aggregates and its verdict is `INCONCLUSIVE`.

The Infisical attempt stopped before agent launch when its filtered Git fetch
stalled. The Langfuse attempt retained two setup failures before agent launch;
a subsequent retry reached a real base-agent launch but was interrupted by the
task runtime before a result artifact was written. No case study is labeled
performance-verified. The checked-in smoke artifacts preserve all results that
the harness successfully retained; missing raw traces from the two early
Browser Use attempts are explicitly treated as a harness defect, not evidence.
