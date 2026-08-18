# Reproducible product workflow

## Level 0: static evidence

```bash
contextlens scan
contextlens diff --base origin/main
```

No credentials, model calls, or instrumentation are required. Findings are
static and not verified.

## Level 1: a few mechanical tasks

Create `.contextlens/evals.json` with one or two tasks and existing test/build
commands, then run:

```bash
contextlens verify .contextlens/evals.json --base origin/main
```

Use repeated trials for nondeterministic agents and require provider usage when
the economic question matters.

## Level 2: checked-in suite and CI

Keep multiple representative tasks under `.contextlens/evals/`, call them from
the config, and use the GitHub Action in verified mode on context-changing PRs
or explicit workflow dispatches.

## Level 3: historical tasks

Importing historical issues/PRs is roadmap work. Prevent solution leakage,
pin repository state, use mechanical hidden checks, and record task selection.

## Deterministic local demo

```bash
python examples/context-regression/run_demo.py
```

The demo uses a temporary Git repository and fixture subprocess, performs two
matched trials, and cleans up. It calls no model service.

## Security boundary

Static commands are local. Verified commands execute the configured agent and
mechanical checks in copied workspaces. Directory copies do not isolate the OS,
network, or credentials. Use a container adapter for untrusted tasks.
