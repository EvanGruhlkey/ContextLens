# Langfuse control study

The manifest pins one `web/**` and one `packages/shared/**` task. Candidates
may come only from ContextLens' conservative static operations. An empty
candidate or rejected optimization is an expected control outcome and must be
retained in the results.

The shared OTEL task is ready: the buggy revision fails the upstream flattened
parameter assertion and the fixed revision passes four tests. The web task has
successful deterministic setup but is blocked by Vitest 4 test-path discovery
on Windows, which reports no test files even while listing the exact file in the
server project. No agent trial has run.
