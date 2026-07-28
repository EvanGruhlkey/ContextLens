from __future__ import annotations

import unittest

from contextlens.benchmark import run_benchmark


class AdaptiveBenchmarkTests(unittest.TestCase):
    def test_adaptive_search_beats_exhaustive_single_source_ablation(self) -> None:
        result = run_benchmark(32)
        self.assertLess(
            result.adaptive_experiments,
            result.exhaustive_experiments,
        )
        self.assertGreater(result.reduction_fraction, 0.5)
        self.assertTrue(result.correctly_retained_critical_source)
        self.assertEqual(result.removable_sources_found, 31)


if __name__ == "__main__":
    unittest.main()
