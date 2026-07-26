# Architecture

## Scope

ContextLens is an experimentation framework, not an agent framework or prompt
management system. It observes an agent's context boundary, produces controlled
replays, evaluates their outcomes, and attributes changes to context sources.

The MVP uses leave-one-source-out ablation:

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
Recorder ──► versioned trace ──► Ablation planner
                                      │ variants
                                      ▼
Task suite ─────────────────────► Replay runner
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

### Ablation planner

Selects removable units and creates experiment variants. The initial unit is a
single context source. Later planners may remove groups, truncate histories, or
sample coalitions to estimate interactions.

### Replay runner

Invokes an adapter with a task and an exact context variant. It owns
concurrency, caching, seeds, timeouts, retries, and provider rate limits. An
adapter must not silently inject unrecorded context.

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
  adapters/      agent and provider integrations
  experiments/   task suites, planning, replay, caching
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

Leave-one-out attribution can misrepresent correlated or interacting sources.
If two sources duplicate the same fact, each may appear useless even though at
least one is required. The MVP will document this limitation and retain enough
run-level data for grouped or Shapley-style analysis later.

