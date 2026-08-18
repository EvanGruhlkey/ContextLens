# GitHub Action and CI

ContextLens supports two deliberately different modes.

## Static CI

Static CI performs no model calls and needs no credentials. It compares the PR
worktree with a Git base and writes a Markdown summary plus JSON artifact.

```yaml
- uses: EvanGruhlkey/ContextLens@main
  with:
    mode: static
    base: ${{ github.event.pull_request.base.sha }}
    max-context-increase: "0.25"
    max-duplicate-increase: "0"
    max-stale-increase: "0"
    targets: "packages/api/src/auth.ts,packages/api/src/user.ts"
    provider: codex
```

All thresholds are optional. A threshold gates only an observed static
property; it does not claim the change harms agent performance.
Without thresholds, static mode is report-only and passes even when context
grows. `targets` enables effective-context delta reporting; it does not create
an additional gate.

## Verified CI

Verified CI runs the checked-in task suite and can incur model/provider cost:

```yaml
- uses: EvanGruhlkey/ContextLens@main
  with:
    mode: verified
    base: ${{ github.event.pull_request.base.sha }}
    config: .contextlens/evals.json
```

Store provider credentials using the normal GitHub Actions secret mechanism
expected by your adapter. Do not place them in the eval config.

## Checkout requirement

Use `fetch-depth: 0` so the action can read the base Git tree:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
```

## Outputs and exit codes

The action writes:

- the human-readable GitHub step summary through `GITHUB_STEP_SUMMARY`;
- `.contextlens/ci-result.json` in the workspace;
- output `result` containing the absolute JSON path.

Static threshold failures and verified regressions exit `4`. Invalid
configuration exits `2`. Verified inconclusive results exit `5` and therefore
fail CI instead of silently passing.

Full examples are under `examples/github-actions/`.
