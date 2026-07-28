"""Transparent token and model-cost accounting."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """USD prices per one million tokens for a specific model."""

    provider: str
    model: str
    input_per_million_usd: float
    output_per_million_usd: float

    def __post_init__(self) -> None:
        if not self.provider or not self.model:
            raise ValueError("provider and model cannot be empty")
        if self.input_per_million_usd < 0 or self.output_per_million_usd < 0:
            raise ValueError("token prices cannot be negative")


@dataclass(frozen=True, slots=True)
class UsageCost:
    """Token usage and its calculated cost."""

    input_tokens: int
    output_tokens: int
    input_cost_usd: float
    output_cost_usd: float

    @property
    def total_cost_usd(self) -> float:
        return self.input_cost_usd + self.output_cost_usd


class CostCalculator:
    """Calculate cost from recorded usage and an explicit pricing snapshot."""

    def __init__(self, pricing: ModelPricing) -> None:
        self.pricing = pricing

    def calculate(self, input_tokens: int, output_tokens: int) -> UsageCost:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        return UsageCost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_usd=(
                input_tokens * self.pricing.input_per_million_usd / 1_000_000
            ),
            output_cost_usd=(
                output_tokens * self.pricing.output_per_million_usd / 1_000_000
            ),
        )

