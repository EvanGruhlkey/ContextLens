"""Task-conditioned observation pruning."""

from contextlens.pruning.model import (
    LineDecision,
    LineReason,
    ObservationKind,
    OmittedRange,
    PruneRequest,
    PruneResult,
    estimate_tokens,
)
from contextlens.pruning.pipeline import ContextPruner
from contextlens.pruning.receipts import Receipt, ReceiptStore
from contextlens.pruning.render import RenderedObservation, render_python_skeleton
from contextlens.pruning.scoring import (
    HttpSemanticScorer,
    SemanticScorer,
    SemanticScores,
)
from contextlens.pruning.structure import StructuralResult, close_python_dependencies

__all__ = [
    "LineDecision",
    "LineReason",
    "ContextPruner",
    "ObservationKind",
    "OmittedRange",
    "PruneRequest",
    "PruneResult",
    "Receipt",
    "ReceiptStore",
    "RenderedObservation",
    "estimate_tokens",
    "render_python_skeleton",
    "HttpSemanticScorer",
    "SemanticScorer",
    "SemanticScores",
    "StructuralResult",
    "close_python_dependencies",
]
