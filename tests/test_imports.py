from __future__ import annotations

import subprocess
import sys


def test_analysis_package_imports_in_fresh_interpreter() -> None:
    completed = subprocess.run(
        (
            sys.executable,
            "-c",
            "from contextlens.analysis import Measurement, PairedAnalyzer",
        ),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_development_eval_harness_imports_in_fresh_interpreter() -> None:
    completed = subprocess.run(
        (
            sys.executable,
            "-c",
            "from evals.cases import SMOKE_CASES; assert SMOKE_CASES",
        ),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
