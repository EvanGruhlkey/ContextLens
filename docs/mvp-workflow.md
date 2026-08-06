# Coding-agent MVP workflow

This revision keeps ContextLens local-first and provider-neutral. It does not
ship an agent or silently call a model provider. The user supplies an
instrumented agent command for recording and a replay adapter command for
experiments.

## Install for development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m mypy src
```

On POSIX systems use `.venv/bin/python`.

## Record a complete run

`contextlens record` sets `CONTEXTLENS_TRACE` for an instrumented agent:

```bash
contextlens record --output traces/fix-184.jsonl -- your-agent "Fix issue #184"
```

The producer uses `TraceWriter.set_trace()` for agent/model/task metadata,
`TraceWriter.add()` for every independently removable context item, and
`TraceWriter.add_step()` for model requests, responses, tool calls, tool
results, evaluations, and system events. Inline content receives a SHA-256
hash. Large content can be externalized through `ArtifactStore`.

Use `SecretRedactor` or explicit `RegexRedactor` instances on the writer.
Redaction happens before inline content or artifacts are persisted.

## Profile the baseline

```bash
contextlens scan traces/fix-184.jsonl \
  --observation traces/fix-184-observation.json \
  --format html \
  --output runs/fix-184-profile.html
```

The profile reports token share, observed usage, redundancy, staleness,
contradiction declarations, relevance, and experiment priority. These values
remain labeled `observed` and `causal: false`; the effect column is empty until
controlled experiments run.

The observation file may include:

```json
{
  "output_text": "Implemented the parser fix and ran tests.",
  "accessed_source_ids": ["agents-md"],
  "commands": ["python -m pytest"],
  "tool_inputs": ["src/parser.py"],
  "changed_files": ["src/parser.py"],
  "task_text": "Fix issue #184",
  "searched_queries": ["parser token handling"]
}
```

## Plan and run experiments

`DeterministicExperimentCoordinator` ranks candidates with an explainable
score based on expected token savings, uncertainty, probability of meaningful
effect, evaluator reliability, and estimated replay cost. A budget counts
actual replay runs. The coordinator schedules at least two matched
baseline/variant pairs for every selected one-item experiment.

The mutation language contains exactly:

- `remove`
- `summarize` with a recorded provider, model, prompt, source hash, and
  generated replacement item
- `lazy_load`, which removes initial context and exposes an explicit retrieval
  list to the adapter
- `scope` to explicit agent IDs and/or workflow phases

`ReplayWorker` applies the mutation, copies the pinned input directory into a
fresh temporary workspace, supplies a separate request and result file, and
records patches and status. The subprocess adapter forwards only an
environment allowlist plus explicitly named secret variables.

The existing `contextlens optimize experiment.json` command remains the
configuration-driven end-to-end command for adaptive search and combined
target-model verification. See [cli.md](cli.md) for its JSON contract.

## Evaluate and estimate effects

`CodingTaskEvaluator` preserves these dimensions:

- task completion
- tests
- build
- type checking
- lint
- patch quality
- patch scope

The default balanced weights are stored in evaluator metadata. Cost and latency
apply bounded penalties only after task quality is scored. Fewer tokens alone
never make a run successful.

Pair externally collected measurements when needed:

```bash
contextlens analyze measurements.json \
  --baseline baseline \
  --ablated without-tool-schemas \
  --equivalence-tolerance 0.01 \
  --format json \
  --output runs/tool-schema-effect.json
```

Effects include means, absolute and relative change, token/cost/latency/tool
changes, variance, confidence interval, paired run count, an honest evidence
label (`strong`, `moderate`, `weak`, or `inconclusive`), and a recommendation.
Failed and timed-out runs remain visible and are not converted into successful
measurements.

## Export a context policy

```bash
contextlens policy runs/latest.json \
  --objective balanced \
  --format yaml \
  --output runs/context-policy.yaml
```

The exporter chooses the strongest finding per source and conservatively maps
unverified findings to `needs_more_evidence`. The in-process model validates
version, objective, rule names, strategies, targets, and token limits. The
portable JSON Schema is at `schemas/context-policy.schema.json`.

## Normalized local store

`ContextLensStore` creates a SQLite database with normalized projects, traces,
steps, context items, profiles, experiments, variants, mutations, replay runs,
evaluations, effects, recommendations, and policies:

```python
from pathlib import Path
from contextlens.storage import ContextLensStore

store = ContextLensStore(Path(".contextlens/contextlens.db"))
store.create_project("calculator", "Calculator")
store.save_trace(agent_trace, steps=steps, context_events=context_events)
```

Content reads require both project ID and trace ID. Deleting a trace cascades
to its steps and context. `claim_replay_job()` uses a unique stable job ID to
prevent duplicate execution. The authoritative executable schema lives in the
wheel; `migrations/0001_normalized_contextlens.sql` documents the initial
migration for deployments with an external migration runner.

## Deterministic demonstration fixture

`tests/fixtures/coding-agent-repo` contains repository instructions,
architecture documentation, git history, multiple tool schemas, noisy terminal
output, a contradictory stale instruction, a coding bug, and an acceptance
test. `tests/test_revision_vertical_slice.py` derives, rather than hardcodes,
these findings from isolated deterministic replay behavior:

- repository instructions are helpful and should be retained;
- the stale contradictory instruction is harmful and should be removed;
- unused tool schemas are neutral and should be lazy-loaded;
- terminal output can be summarized;
- phase-specific context can be scoped.

## Legacy changes and compatibility

No working subsystem was deleted. The JSONL source recorder, artifact store,
profiler, replay adapter, workspace isolation, adaptive search, optimizer,
paired analysis, CLI commands, and report renderers remain.

The removal-only `ContextVariant.removed_source_ids` field is retained as a
compatibility shorthand and is translated into explicit remove mutations.
One-run labels remain observed hypotheses. The new deterministic coordinator
complements, rather than replaces, adaptive group search.

## Known limitations and next steps

- Directory copies isolate files but not the kernel, network, process tree, or
  filesystem outside the workspace. Add a Docker-backed snapshot/runtime
  implementation before running untrusted agents.
- The standard-library SQLite build is not encrypted. Use encrypted disks or a
  SQLCipher-backed service for stronger at-rest guarantees.
- Context retention is represented per project but automatic expiry is not yet
  scheduled.
- Lazy retrieval accounting depends on the adapter returning requested source
  IDs, retrieval tokens, and retrieval latency.
- Summarization requires an explicitly configured summarizer; ContextLens does
  not choose or contact a provider.
- There is no multi-user server, authentication system, or live frontend in
  this repository. The self-contained HTML report is the current dashboard.
- Git commit pinning is recorded but `DirectorySnapshot` does not itself fetch
  a missing commit. Add a `GitSnapshot` implementation and Docker runtime next.
- Trace-constrained replay is intentionally deferred; all causal claims in the
  MVP should come from closed-loop isolated replays.
- Sequential stopping currently lives in adaptive search. The new one-item
  paired planner uses a fixed repeat count; connect completed effects back to
  the planner for online stopping next.
