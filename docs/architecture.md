# Architecture

## Product boundary

ContextLens is CI and regression testing for repository-owned AI-agent context.
Its central question is:

> What was the causal effect of changing the agent's context?

It is not a generic observability dashboard, prompt compressor, AGENTS.md
linter, agent framework, or arbitrary eval platform. Static analysis is the
zero-friction front door; controlled A/B agent execution is the differentiator.

## Data flow

```text
Repository worktree ── discovery ── static scan ── review candidates
        │                    │
Git base tree ───────────────┴───── context diff
        │
        ├── base context ─────┐
        │                     ├─ isolated matched replays ─ mechanical evaluator
        └── candidate context ┘                         │
                                                        ▼
                             quality + economics + behavior + performance
                                                        │
                                                        ▼
                                   PASS / WARN / REGRESSION / INCONCLUSIVE
                                                        │
                                             verified minimization / CI gate
```

The base and candidate tasks use the same workspace snapshot, task, agent
identity, model settings, tools, sandbox contract, evaluator, timeout, and
trial policy. Only the context tuple changes.

## Layers

### Repository product layer

`repository.py` discovers context by convention and performs conservative
static analysis. It reads base content from Git objects without checking out a
branch. Repository-wide footprint and target-effective context are distinct;
provider resolvers label scope semantics as documented or approximated.

`bootstrap.py` detects common repository ecosystems, mechanical commands, and
available adapters for `contextlens init`. It never executes project code while
detecting configuration and emits explicit TODOs when evidence is insufficient.

`experiments/paired_runner.py` owns the shared `PairedAgentExperiment` and
`ContextExperimentRunner` execution primitive. It creates fresh isolated
workers, alternates trial order, preserves explicit pairing, classifies task
failures separately from infrastructure errors, and emits a reproducible
manifest plus raw evidence.

`regression.py` resolves task-effective base and candidate context, invokes the
shared paired runner, normalizes the raw trials, excludes infrastructure-invalid
runs from causal aggregates, and applies fail-closed quality/economics verdicts.
Verified minimization and the historical case-study harness reach agents
through this same path.

`telemetry.py` normalizes common OpenAI-style, Anthropic-style, and generic
usage objects. Cached, uncached, cache-write, visible output, and reasoning
tokens stay distinct. Pricing is explicit and dated.

`minimize.py` uses explainable static signals only to generate and prioritize
candidates. Each footprint-reducing edit is tested independently; passing edits
receive a separate combined target-model verification to catch interactions.
It recommends a patch only after final PASS and never edits source files.

`ci.py` provides stable static/verified exit semantics and machine-readable
results. `action.yml` exposes that contract as a composite GitHub Action.

### Retained experimentation layer

The product pivot preserves the technically difficult original system:

- `trace/`: versioned JSONL context/run/step records, artifacts, redaction;
- `profiler/`: deterministic one-run utilization and duplication signals;
- `experiments/`: adapters, isolated workspaces, explicit mutations, repeated
  paired runners, adaptive search, caching, retries, and resource limits;
- `evaluators/`: exact, test-result, callable, recorded, and coding-task
  mechanical evaluators;
- `analysis/`: paired bootstrap effects, uncertainty, savings, and cost;
- `optimization/`: candidate construction, screening, predictors, and combined
  target-model verification;
- `storage/`: normalized project-scoped SQLite persistence;
- `reports/`: terminal, JSON, CSV, and self-contained HTML reports;
- `policy.py` and `runtime.py`: validated policy export and fail-closed runtime
  application.

Legacy commands call these systems directly. New commands orchestrate them at
the repository-context boundary.

## Context source mapping

Discovered repository files map to immutable `ContextSource` objects:

| Repository source | Internal kind | Scope |
| --- | --- | --- |
| `AGENTS.md`, `CLAUDE.md`, Copilot/Cursor rules, skills | `repo_instruction` | containing directory or declared convention |
| MCP config and static tool schemas | `tool_schema` | containing directory |

The source ID is stable for a repository path. Content hashes detect drift.
Static byte estimates are labeled; recorded provider token counts take
precedence when available.

## Evidence semantics

- **Observed/static**: deterministic footprint or syntax/repository facts.
- **Candidate/static**: a proposed mutation generated from static signals.
- **Screening**: cheaper or substitute-model prioritization evidence.
- **Verified/target model**: controlled task replay on the configured agent.
- **Regression**: observed quality or economics breached policy.
- **Inconclusive**: evidence was missing or insufficient.

"Not observed being used" never means "safe to remove." Independent safe
removals can interact, so combined candidates are reverified.

## Economics objective

Context footprint is not the economic objective. When available, ContextLens
models:

```text
uncached input cost
  + cached input cost
  + cache-write input cost
  + visible output cost
  + reasoning cost
  + optional latency policy
```

subject to quality remaining within tolerance and zero catastrophic
regressions. If dollar pricing is incomplete, token categories are reported
separately.

## Reproducibility contract

A trustworthy verification records:

- Git base, candidate content hashes, ordered context manifest;
- per-task target paths, context provider, exact effective source paths,
  effective initial tokens, resolution mode, and scope warnings;
- task, workspace digest, agent/provider/model identity, and settings;
- evaluator/check commands, sampling policy, trial, and run IDs;
- raw success/score evidence and failed attempts;
- injected context, provider usage categories, behavior, and latency;
- ContextLens/adapter versions and explicit pricing snapshot when used.

Remote models are not assumed deterministic. Repeated matched trials expose
variance; they do not guarantee it disappears.

## Telemetry adapters

The internal trace remains supported but is not required. Provider usage
objects normalize directly through `telemetry.py`. The adapter boundary is
intended to accept generic JSON and OpenTelemetry GenAI spans without changing
the regression model. See [adapters](adapters.md).

## Security and isolation

ContextLens is local-first. Traces and result JSON can contain prompts, code,
commands, and model output.

- Built-in redaction runs before ContextLens persistence when configured.
- Reports exclude raw context by default.
- Subprocess environment variables use an allowlist plus explicit secrets.
- Directory-copy workers isolate filesystem mutations only.
- Experimental snapshots omit discovered native context files; the selected
  effective context is supplied directly by the runner.
- Codex workers are new ephemeral processes with user config and native rule
  loading disabled.
- Hidden study graders and their configuration remain outside the agent-visible
  snapshot and are injected only after the coding agent exits.
- Strong OS/network/credential isolation requires a container or equivalent
  adapter supplied by the deployment environment.

Static scan/diff perform no network access or model calls.
