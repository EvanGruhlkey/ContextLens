"""Statistical analysis and resource accounting."""

from contextlens.analysis.costs import CostCalculator, ModelPricing, UsageCost
from contextlens.analysis.paired import (
    EffectVerdict,
    EvidenceScope,
    Measurement,
    PairedAnalyzer,
    PairedEffect,
)
from contextlens.analysis.savings import (
    SavingsAction,
    SavingsAnalyzer,
    SavingsRecommendation,
    Workload,
)

__all__ = [
    "CostCalculator",
    "EffectVerdict",
    "EvidenceScope",
    "Measurement",
    "ModelPricing",
    "PairedAnalyzer",
    "PairedEffect",
    "SavingsAction",
    "SavingsAnalyzer",
    "SavingsRecommendation",
    "UsageCost",
    "Workload",
]
