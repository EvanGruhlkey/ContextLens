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
5. export of the optimizer-produced context policy;
6. fresh full-context, ContextLens, and matched-random verification runs;
7. per-case and aggregate reports with token, latency, test, and overhead data.

Context is discovered by repository conventions (`AGENTS.md`, root project
metadata, project documentation, the tracked-file map, the pinned commit, and
setup output). Case authors do not hand-pick relevant source files or construct
a policy. Removing eager context never deletes repository files: every worker
can still search and open the isolated checkout with its normal tools.

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

ChatGPT-authenticated Codex reports token usage but not a per-call USD charge,
so reports calculate token break-even and mark dollar cost unavailable.
