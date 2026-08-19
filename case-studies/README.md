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

## Historical bug-fix agent study

`run.py` adds a second, task-performance study over six bugs pinned before
candidate outcomes across Browser Use, Infisical, and Langfuse:

```bash
python case-studies/run.py list
python case-studies/run.py prepare browser-use browser-use-redaction-cascade
python case-studies/run.py validate --install browser-use browser-use-redaction-cascade
python case-studies/run.py run --install --trials 1 browser-use browser-use-redaction-cascade
```

Preparation uses detached worktrees at exact commits. Validation stages hidden
graders only after execution, requires dependency setup to succeed, and requires
the grader to fail on the buggy revision and pass on the upstream fixed revision.
Trial counts are restricted to one smoke trial or three matched trials per
variant, with at most 36 agent runs across the locked study.

See `agent-study-status.md` for observed validation evidence and current
infrastructure blockers. It reports zero agent trials; candidate token deltas
must not be interpreted as model-quality or API-cost savings.
