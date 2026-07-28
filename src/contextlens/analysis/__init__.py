"""Statistical analysis and resource accounting."""

from contextlens.analysis.costs import CostCalculator, ModelPricing, UsageCost
from contextlens.analysis.paired import (
    EffectVerdict,
    EvidenceScope,
    Measurement,
    PairedAnalyzer,
    PairedEffect,
)

__all__ = [
    "CostCalculator",
    "EffectVerdict",
    "EvidenceScope",
    "Measurement",
    "ModelPricing",
    "PairedAnalyzer",
    "PairedEffect",
    "UsageCost",
]
