# ContextLens roadmap

ContextLens is a context profiler and adaptive experiment coordinator for AI
agents. It should provide useful evidence after one normal run, then spend
additional model calls only where they are likely to change a decision.

Every result is labeled by evidence level:

- **Observed** — directly measured during the original run, such as tokens,
  citations, access, position, duplication, and tool use.
- **Predicted** — an estimate based on observed signals and prior experiments.
- **Verified** — a causal effect measured through controlled counterfactual
  replays.

## Milestone 1 — Foundation ✅

- Define the product boundary and local-first architecture.
- Establish the Python package and contribution conventions.
- Document recording, replay, evaluation, and reporting boundaries.

## Milestone 2 — Context recording ✅

- Implement the versioned context-source data model.
- Store ordered traces as inspectable JSONL.
- Store large payloads as verified content-addressed artifacts.
- Support configurable redaction before persistence.

## Milestone 3 — One-run profiler ✅

Deliver useful results without rerunning the task.

- Detect which sources were cited, copied, opened, or used by tool calls.
- Measure tokens, context position, age, retrieval rank, and repeated content.
- Detect exact and semantic duplication between sources.
- Connect output claims, commands, and file edits to likely supporting context.
- Report `used`, `unused`, `duplicated`, and `uncertain` signals without
  presenting them as causal effects.
- Define optional model-internals adapters for log-probability, attention, or
  gradient signals while keeping the core compatible with black-box APIs.

## Milestone 4 — Isolated replay workers ✅

Create trustworthy, reproducible counterfactual runs.

- Define task suites, agent adapters, and evaluator contracts.
- Snapshot the initial task state.
- Run every worker in an isolated temporary workspace or Git worktree.
- Hold the model, task, tools, settings, evaluator, and initial files constant.
- Change only the selected context configuration.
- Capture outputs, patches, commands, tests, tokens, latency, and errors.
- Add concurrency, timeout, retry, caching, and spending controls.

## Milestone 5 — Adaptive context search ✅

Find valuable context without naively running one worker per source.

- Establish a full-context baseline.
- Begin with meaningful groups such as tool schemas, memories, repository
  instructions, message history, and command output.
- Run group ablations in parallel.
- Split groups only when their removal produces a meaningful result.
- Prioritize experiments by expected information gain and possible token
  savings.
- Stop early when confidence is sufficient or further testing is not worth its
  projected cost.
- Support individual leave-one-out verification when precision is needed.
- Preserve grouped and interaction evidence so duplicated sources are not
  incorrectly declared useless.

## Milestone 6 — Evaluation and statistics ✅

- Support exact, programmatic, test-suite, human, and model-graded evaluators.
- Track success, quality, latency, input/output tokens, and estimated cost.
- Compare paired baseline and experimental trials.
- Compute effect sizes, uncertainty intervals, and stability warnings.
- Distinguish screening evidence from production-model verification.
- Support repeated trials for nondeterministic models.

## Milestone 7 — Context optimization ✅

Recommend the best context configuration for a chosen objective.

- Optimize for maximum quality, minimum cost, minimum latency, or a token
  budget.
- Use fixed-answer log-probability scoring as a cheaper screening method when
  an adapter supports it.
- Verify promising configurations with real task reruns.
- Learn a context-value predictor from previous verified experiments.
- Use predictions to reduce future experiments while periodically
  recalibrating them.
- Never promote predicted value to verified value without an intervention.

## Milestone 8 — Reports and CLI ✅

- Produce terminal, JSON, CSV, and self-contained HTML reports.
- Show observed, predicted, and verified findings separately.
- Rank helpful, neutral, harmful, duplicated, and uncertain context.
- Show quality gained or lost per 1,000 tokens and per dollar.
- Display the experiment tree, stopping decisions, sample sizes, and evidence.
- Provide drill-down from aggregate findings to individual worker runs.

Target workflow:

```bash
contextlens record --output traces/task-001.jsonl -- your-agent-command
contextlens scan traces/task-001.jsonl
contextlens optimize experiments/example.json --format json --output runs/latest.json
contextlens report runs/latest
```

## Milestone 9 — Open-source release preparation ✅

- Ship deterministic fixtures and end-to-end examples.
- Add CI, issue templates, and a code of conduct.
- Document adapters, isolation requirements, and third-party data handling.
- Benchmark adaptive search against exhaustive leave-one-out evaluation.
- Build and validate the initial MIT-licensed release artifacts.

## Publication

Maintainer action is still required to choose the final repository/package
destinations, push `main`, create the `v0.1.0` tag, and publish the validated
artifacts. ContextLens does not store publishing credentials or upload packages
from local release validation.

## Product guardrails

- One-run signals describe apparent utilization, not causal value.
- Parallelism reduces elapsed time, not model-token cost.
- Cheap-model experiments are screening evidence until verified on the target
  model.
- Workers must not share mutable task state.
- Reports must include experiment cost alongside projected future savings.
- Production savings require target-model evidence and a declared workload;
  individual projections are not additive until the combined context is tested.
- Users set hard limits for workers, concurrency, tokens, dollars, and time.
