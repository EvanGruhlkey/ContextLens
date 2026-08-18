# Real repository context-change corpus

This corpus pins seven public, historical changes that directly add, remove,
or modify recognized agent context. It is deliberately selection-neutral: it
includes additions, removals, and rewrites across six repositories.

Run a credential-free static reproduction without checking out either tree:

```bash
python case-studies/run_static.py --case vscode-add-agents \
  --output case-studies/reports/vscode-add-agents.json
```

The harness fetches only the two pinned Git trees, discovers context in each
immutable revision, and emits an observed/static ContextLens diff. These
reports are real repository evidence, but they are **not agent-performance
results**.

`study_status: static_ready` is intentional. A case must add realistic tasks,
mechanical graders, agent/model metadata, repeated matched trials, all failures,
and raw usage evidence before it can be labeled `verified`. We do not infer
ground truth from a commit message or publish unrun benchmark numbers.

The existing `evals/` harness supplies isolated acquisition, mechanical
grading, repeated trials, and result validation for that next stage.
