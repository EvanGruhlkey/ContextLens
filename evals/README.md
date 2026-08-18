# Real-repository evaluation

The primary evaluation runs coding agents against public repositories at exact
commits. Synthetic fixtures remain unit tests for ContextLens internals; they
are not benchmark cases.

For every case the harness fetches only the pinned commit into an acquisition
directory, removes the remote, verifies that no other commit is reachable, and
then uses the production ContextLens path:

1. a fresh full-context agent run and mechanical verification;
2. production trace recording and one-run profiling;
3. adaptive paired replays in fresh workspaces;
4. production paired analysis and optimizer verification;
5. export of the optimizer-produced candidate policy;
6. at least three fresh full-context, ContextLens, and genuinely distinct
   token-matched-random verification runs;
7. a deployment gate that rejects the candidate after any observed final
   quality regression and exports a non-pruning policy instead;
8. per-case and aggregate reports with injected-context, provider-token,
   latency, test, and overhead data.

Context is discovered by repository conventions (`AGENTS.md`, root project
metadata, project documentation, the tracked-file map, the pinned commit, and
setup output). Case authors do not hand-pick relevant source files or construct
a policy. Removing eager context never deletes repository files: every worker
can still search and open the isolated checkout with its normal tools.

## Primary result

ContextLens is a context-token saver. Its headline metric is the fraction of
preloaded, injected context tokens removed by the final deployable policy while
mechanical task quality remains within tolerance. Total provider input is a
secondary diagnostic because it also includes system prompts, cache behavior,
reasoning, tool calls, and context the agent chooses to open later.

`aggregate.json` reports the headline under `deployable_context_effect`. Cases
whose candidate regresses fall back to full context, so rejected removals never
contribute to the deployable savings percentage.

The JSON-compatible `.yaml` files under `evals/cases/smoke/` pin six cases: two
tasks each for AWS Powertools for Lambda, Spotify Luigi, and Microsoft tslib.
These company-owned repositories were selected by the recorded seeded,
language-stratified procedure in `selection.json`. Hidden verification commands
are absent from public case manifests and worker prompts.

Run one case first:

```bash
python -m evals.run_case --case evals/cases/smoke/aws-eventbridge-replay.yaml
python -m evals.validate_run evals/artifacts/<run-id>
```

After that succeeds, run the bounded six-case suite:

```bash
python -m evals.run_suite --suite smoke
python -m evals.validate_run evals/artifacts/<run-id>
```

Each model call uses a fresh `codex exec --ephemeral --json` process and fresh
workspace. Responses are not cached or retried. The raw provider JSONL, exact
prompt, context partition, token usage, commands, tool events, file changes,
test output, policy, trace, reports, and checksums are retained under
`evals/artifacts/<run-id>/`. `case-summary.txt` is the concise result requested
for each repository task.

Both commands default to three trials. `candidate-context-policy.*` records the
configuration measured by the final controls. `context-policy.*` is the safe
deployment artifact; if a final trial regresses, it contains no exclusion
mutation and `deployment-decision.json` explains the rejection.

ChatGPT-authenticated Codex reports token usage but not a per-call USD charge,
so reports calculate token break-even and mark dollar cost unavailable.
