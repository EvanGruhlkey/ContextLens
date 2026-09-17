# Benchmarks

## Free GPU execution

Upload `benchmarks/free_gpu.ipynb` to Kaggle or Google Colab. Select a free
GPU runtime (and enable Internet on Kaggle), then run the cells. The notebook
embeds the current audited source snapshot so unpublished local changes are
included. It installs ContextLens, runs the structural audit and three repeated
real-model source reads, and exports `contextlens-results.zip`. No paid API or
Modal deployment is needed. Free GPU availability depends on the notebook service.

The runtime benchmark uses exact `o200k_base` token counts including omission
markers, records backend failures as invalid, and separates first-request from
subsequent latency. These are observation metrics, not total provider usage or
downstream agent quality. The structural audit uses oracle evidence rather than
the neural model. Its synthetic savings are not production savings.

Local commands (install with `pip install -e ".[dev,benchmark]"`):

```bash
python -m benchmarks.pruning_quality --output results/structural.json
python benchmarks/pruning_runtime.py --output results/runtime.json
```

The older `evals/` agent harness measures repository context policies rather
than the current observation pruning middleware, so its results cannot validate
the current pruner without a new integration.

## Free T4 runtime variant

The default upstream runtime exhausted a free Colab T4 on large reads. The
explicit experimental variant completed all nine reads with the original
8,192-token window:

```bash
python -m benchmarks.pruning_t4 --output results/runtime-efficient.json
```

It uses memory-efficient PyTorch SDPA and expands grouped keys/values for that
kernel. Floating-point equivalence is unverified; reports label the variant.
See `docs/benchmark-audit.md` and the saved raw JSON reports for actual results.

`pruning_qa.py` is an exploratory post-hoc three-question comparison using a
free open coding model. It is a comprehension smoke test, not an agent benchmark.

> **Planner benchmark only. This does not measure end-to-end LLM task
> performance or production token savings.**

## Adaptive versus exhaustive ablation

Run:

```bash
python benchmarks/adaptive_vs_exhaustive.py
```

The deterministic fixture contains 32 equal-cost context sources and one
critical source. Removing any group containing that source reduces quality.
Every other source can be removed.

Expected result for version 0.1.0:

```json
{
  "sources": 32,
  "adaptive_experiments": 12,
  "exhaustive_experiments": 33,
  "experiments_saved": 21,
  "reduction_fraction": 0.6363636363636364,
  "correctly_retained_critical_source": true,
  "removable_sources_found": 31
}
```

This benchmark validates planner behavior and query count. It is not evidence
that real model tasks will achieve the same reduction. Real savings depend on
context interactions, evaluator noise, group structure, and stopping budgets.
