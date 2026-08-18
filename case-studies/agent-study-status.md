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

Agent runs completed: **0**. The only discovered `codex` executable belongs to
the WindowsApps desktop bundle and returns `Access is denied` when launched as a
CLI. Accordingly there are no pass-rate, duration, usage, or cost results and no
candidate is labeled performance-verified.
