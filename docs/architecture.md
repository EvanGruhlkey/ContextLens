# Architecture

## Scope

ContextLens is an experimentation framework, not an agent framework or prompt
management system. It observes an agent's context boundary, produces controlled
replays, evaluates their outcomes, and attributes changes to context sources.

ContextLens separates one-run utilization signals from causal attribution.
Observed signals can prioritize experiments, but a source is called helpful or
harmful only after a controlled intervention.

For a verified ablation:

```text
effect(source) = score(full context) - score(context without source)
```

A positive effect means removing the source reduced quality, so the source was
helpful. A negative effect means removing it improved quality, so the source was
harmful. Reports must state this sign convention explicitly.

## System boundaries

```text
Agent/runtime
    │ events
    ▼
Recorder ──► versioned trace ──► One-run profiler
                                      │ observed signals
                                      ▼
Task suite ─────────────────────► Adaptive planner
                                      │ selected variants
                                      ▼
                                 Isolated workers
                                      │ outcomes
                                      ▼
Evaluator ──────────────────────► Analysis ──► Reports
```

### Recorder

Captures context items in order at the point they enter a model request. Each
item has a stable ID, a source kind, content or a content reference, token
accounting, provenance, sensitivity metadata, and optional tags.

### Trace store

Persists immutable JSONL event streams plus a manifest. JSONL keeps traces
streamable and inspectable; a schema version permits migrations. Large or
binary payloads are stored as content-addressed artifacts.

### One-run profiler

Measures apparent utilization, duplication, provenance, position, and token
cost during the original task. These signals are useful for prioritization but
are not causal effects.

### Adaptive planner

Selects the next experiment using observed signals, prior results, expected
information gain, and experiment cost. It starts with meaningful groups, splits
only promising groups, and can fall back to individual leave-one-out tests.

### Isolated replay workers

Invokes an adapter with a task and an exact context variant. It owns
concurrency, caching, seeds, timeouts, retries, and provider rate limits. An
adapter must not silently inject unrecorded context. Every worker receives the
same starting snapshot and an isolated mutable workspace.

### Evaluator

Maps an outcome to one or more numeric scores and evidence. Deterministic
evaluators are preferred. Model graders must record their prompts, models, and
settings as experiment dependencies.

### Analysis and reporting

Compares paired baseline and ablated results, reports uncertainty, and separates
quality effects from token and latency effects.

## Context source taxonomy

The core schema will start with:

- `system_instruction`
- `agent_instruction` (including `AGENTS.md`)
- `memory`
- `tool_schema`
- `file`
- `message`
- `command_output`
- `search_result`
- `retrieval`
- `git_history`
- `architecture_decision`
- `custom`

Source kind is descriptive, not the ablation identity. Multiple files or
messages can share a kind while retaining distinct IDs and provenance.

## Repository layout

```text
src/contextlens/
  trace/         schema, events, serialization, redaction
  profiler/      one-run usage and duplication signals
  adapters/      agent and provider integrations
  experiments/   task suites, adaptive planning, workers, caching
  evaluators/    scoring contracts and built-ins
  analysis/      paired statistics and attribution
  reports/       terminal and export formats
  cli.py
tests/
docs/
examples/
```

## Reproducibility contract

An experiment result is valid only when it records:

- Context trace and task suite content hashes.
- Agent/model/provider identity and sampling settings.
- Evaluator identity and configuration.
- Context variant and removed source IDs.
- Random seed, attempt number, timestamps, latency, and errors.
- Context tokens, output tokens, and token-counting method.
- ContextLens and adapter versions.

Exact determinism is not promised for remote models. ContextLens instead makes
variance visible through repeated paired trials and confidence intervals.

## Security and privacy

Traces are sensitive by default. The implementation will:

- Store locally unless the user explicitly configures a remote backend.
- Support redaction before persistence.
- Avoid recording environment variables and credentials by default.
- Mark content with sensitivity and retention metadata.
- Never include raw trace content in reports unless explicitly requested.

## Important limitation

Any one-run attribution can show apparent utilization but cannot establish that
a source improved the result. Even controlled leave-one-out attribution can
misrepresent correlated or interacting sources: if two sources duplicate the
same fact, each may appear useless even though at least one is required.
ContextLens therefore preserves evidence levels, grouped results, and run-level
data for later interaction analysis.

## Implemented coding-agent MVP revision

The portable trace stream now optionally includes an `AgentTrace` aggregate and
ordered `TraceStep` records in addition to context events. The normalized local
index is SQLite:

```text
projects
  -> traces
     -> trace_steps
     -> context_items -> context_profiles
     -> experiments -> experiment_variants -> mutations
                    -> replay_runs -> evaluation_results
                    -> effect_estimates
     -> recommendations
  -> context_policies
```

All content-bearing trace reads are project scoped. Core query fields are
columns; JSON is limited to provider metadata, evidence, and extensible
parameters.

The replay mutation boundary is explicit:

```text
baseline context
  -> remove | summarize | lazy_load | scope
  -> initial context + retrievable context
  -> isolated workspace and conversation
  -> adapter outcome + file patch + retrieval accounting
  -> coding evaluator
  -> paired effect
```

`DeterministicExperimentCoordinator` produces stable one-item experiments and
matched job IDs. `ExperimentLifecycle` enforces pending, running, evaluating,
and terminal states. The existing adaptive group planner remains available for
later interaction search.

See [contextlens-revision-plan.md](contextlens-revision-plan.md) for the
assessment and [mvp-workflow.md](mvp-workflow.md) for reproducible commands,
security boundaries, compatibility notes, and limitations.
