"""Real-model evaluation fixtures for the production ContextLens pipeline."""

import sys
from pathlib import Path

# Keep the documented ``python -m evals.run`` command usable from a source
# checkout without requiring an editable install in the caller's interpreter.
_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from evals.cases import (  # noqa: E402
    DEVELOPMENT_CASES,
    HELDOUT_CASES,
    SMOKE_CASES,
    EvalCase,
    EvalCategory,
    EvalSuite,
    all_cases,
    get_case,
    get_suite,
)

__all__ = [
    "DEVELOPMENT_CASES",
    "HELDOUT_CASES",
    "SMOKE_CASES",
    "EvalCase",
    "EvalCategory",
    "EvalSuite",
    "all_cases",
    "get_case",
    "get_suite",
]
