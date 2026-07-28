# Context optimization

Context optimization turns adaptive-search findings into a deployable context
configuration.

## Why combined verification matters

Adaptive search tests groups independently. Two removals that are safe alone
may be harmful together. ContextLens therefore combines proposed removals and
runs the resulting configuration through the target-model worker and evaluator
before accepting it.

```text
Individually safe removals
          |
          v
 Combined candidate ----> optional fixed-answer screening
          |
          v
 Target-model replay ----> evaluator ----> accept or reject
```

## Objectives

`OptimizationPolicy` supports:

- `max_quality`
- `min_cost`
- `min_latency`
- `token_budget`
- `quality_per_dollar`

All objectives retain a quality tolerance. Cost and latency objectives compare
the verified candidate with recorded baseline resources. Token-budget
candidates must fit the requested budget. Quality-per-dollar compares the
quality/cost ratio rather than quality or cost alone.

```python
from contextlens.optimization import (
    ContextOptimizer,
    OptimizationObjective,
    OptimizationPolicy,
)

optimizer = ContextOptimizer(
    context_sources,
    profiles=one_run_report.profiles,
    predictor=value_predictor,
)

policy = OptimizationPolicy(
    OptimizationObjective.MIN_COST,
    quality_tolerance=0.01,
    max_cost_usd=0.05,
)

candidate = optimizer.propose(search_report, policy)
verified = optimizer.verify(
    candidate,
    coordinator=replay_coordinator,
    evaluator=evaluator,
    score_name="quality",
    baseline_score=0.90,
    baseline_cost_usd=0.08,
    policy=policy,
)
```

An accepted configuration is labeled `target_model`. A failed replay or missing
objective measurement cannot produce an accepted recommendation.

## Fixed-answer screening

`FixedAnswerScorer` allows compatible providers or local models to score the
original answer under full and reduced contexts without generating a new
answer. This can cheaply prioritize candidates, but its result is always
`screening` evidence and never replaces a real task replay.

## Learned context value

`ContextValuePredictor` is a small dependency-free ridge model trained on:

- One-run profiler features.
- Verified baseline-minus-ablation effects.

Features include token size, position, overlap, duplication, age, retrieval
rank, source kind, and one-run usage label. Positive predictions mean the
source is likely helpful; negative predictions mean its removal may help.

The model is intentionally simple and serializable so predictions remain
inspectable. Predictions are labeled `predicted`, include residual uncertainty,
and cannot become verified merely by being reused.

For a strict token budget, the optimizer first applies individually verified
removals. If that is insufficient, it can use the predictor to select additional
low-value sources while refusing to remove sources already verified as
important. The whole combined candidate must then pass target-model
verification.

`RecalibrationPolicy` requests another verified experiment when:

- The predictor has too little training evidence.
- Too many predictions have been used since the last verification.
- Prediction uncertainty exceeds a configured limit.

## Evidence retained

Every candidate distinguishes:

- Individually verified removals.
- Predictor-added removals.
- Retained source IDs.
- Tokens retained and removed.
- Objective and rationale.

Every verification records quality change, objective values, objective
improvement, replay evidence, evaluator evidence, resource limits, and explicit
rejection reasons.

