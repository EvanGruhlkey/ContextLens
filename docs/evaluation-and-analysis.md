# Evaluation, statistics, and cost

ContextLens separates outcome scoring from statistical comparison.

## Evaluators

Built-in evaluators include:

- `ExactMatchEvaluator` for normalized expected answers.
- `TestResultsEvaluator` for test evidence recorded by an agent adapter.
- `CallableEvaluator` for programmatic checks or model graders.
- `RecordedEvaluator` for human or externally collected scores.

Every evaluator returns numeric scores plus inspectable evidence and metadata.
Model graders should record their grader model, prompt version, settings, and
sampling configuration in evaluation metadata.

```python
from contextlens.evaluators import ExactMatchEvaluator

evaluator = ExactMatchEvaluator({"task-1": "expected answer"})
evaluation = evaluator.evaluate(task, replay_result)
```

Missing test evidence scores as unsuccessful rather than silently passing.

## Paired measurements

`Measurement` joins a replay result with one evaluator score. A measurement
identifies its task, repeated trial, context variant, quality, success, tokens,
cost, latency, and evidence scope.

Baseline and ablated measurements are paired by `(task_id, trial_id)`. Unmatched
runs do not influence the result. Duplicate pairs are rejected.

```python
from contextlens.analysis import Measurement, PairedAnalyzer

effect = PairedAnalyzer(
    confidence=0.95,
    bootstrap_samples=2_000,
    random_seed=42,
).analyze(
    measurements,
    baseline_variant_id="baseline",
    ablated_variant_id="without-tool-schemas",
)
```

The effect sign is:

```text
effect = baseline score - ablated score
```

- Positive: the removed context was helpful.
- Negative: the removed context was harmful.
- An interval crossing zero: uncertain.

With multiple tasks, ContextLens averages repeated trials within each task
before bootstrapping. This prevents tasks with more repetitions from dominating
the aggregate result. With only one task, repeated trials become the bootstrap
units.

A single pair is always reported as uncertain. Reports warn about fewer than
five analysis units, unstable baselines, and effects that vary substantially
between trials.

## Screening versus target-model evidence

Every measurement is explicitly labeled:

- `screening` for experiments run with a cheaper or substitute model.
- `target_model` for the actual model being optimized.

ContextLens refuses to combine these scopes in one paired effect. Screening can
prioritize later experiments but cannot become target-model verification.

## Resource effects

The paired result also reports:

- Input and output tokens saved by ablation.
- Cost saved by ablation.
- Latency saved by ablation.
- Baseline and ablated success rates.
- Quality effect per 1,000 input tokens.

Positive “saved” values mean the ablated variant used fewer resources.

## Pricing

`CostCalculator` calculates cost from a caller-provided `ModelPricing` snapshot:

```python
from contextlens.analysis import CostCalculator, ModelPricing

calculator = CostCalculator(
    ModelPricing(
        provider="provider",
        model="model",
        input_per_million_usd=2.00,
        output_per_million_usd=8.00,
    )
)
usage = calculator.calculate(input_tokens=10_000, output_tokens=1_000)
```

ContextLens does not bundle live provider prices. Experiment manifests should
record the exact pricing values and date used so historical reports remain
reproducible.

