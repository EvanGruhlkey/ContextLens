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
ChatGPT authentication. The Browser Use redaction-cascade study now has three
infrastructure-valid pairs: both variants passed 3/3, all six hidden graders
ran, all six workspaces were removed, and the verdict is `PASS`. Median paired
provider-input delta was -236,395 tokens (-55.7%); see `RESULTS.md` for quality-
first reporting, trial-level deltas, limitations, and raw evidence.

The Infisical attempt stopped before agent launch when its filtered Git fetch
stalled. The Langfuse attempt retained two setup failures before agent launch;
a subsequent retry reached a real base-agent launch but was interrupted by the
task runtime before a result artifact was written. No Infisical or Langfuse
case study is labeled performance-verified. The checked-in artifacts preserve all results that
the harness successfully retained; missing raw traces from the two early
Browser Use attempts are explicitly treated as a harness defect, not evidence.
