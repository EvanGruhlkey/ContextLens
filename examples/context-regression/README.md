# Context regression demo

This deterministic demo creates a temporary Git repository with duplicated
`AGENTS.md` guidance, removes the nested duplicate in the candidate worktree,
and runs the complete public workflow:

```bash
python examples/context-regression/run_demo.py
```

It calls no model API and needs no credentials. The fixture subprocess reports
synthetic provider usage so the example can exercise cache-aware `verify`
output. Its numbers demonstrate the workflow only; they are not a ContextLens
benchmark or production-savings claim.
