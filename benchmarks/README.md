# Benchmarks

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

