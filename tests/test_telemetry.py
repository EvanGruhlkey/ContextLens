from __future__ import annotations

import pytest

from contextlens.telemetry import (
    PricingSnapshot,
    calculate_usage_cost,
    normalize_otel_genai_attributes,
    normalize_provider_usage,
)


def test_normalizes_openai_cached_and_reasoning_tokens() -> None:
    usage = normalize_provider_usage(
        {
            "input_tokens": 1_000,
            "input_tokens_details": {"cached_tokens": 700},
            "output_tokens": 200,
            "output_tokens_details": {"reasoning_tokens": 150},
        },
        provider="openai",
    )

    assert usage.input_tokens == 1_000
    assert usage.cached_input_tokens == 700
    assert usage.uncached_input_tokens == 300
    assert usage.output_tokens == 50
    assert usage.reasoning_tokens == 150


def test_normalizes_anthropic_cache_read_and_write_tokens() -> None:
    usage = normalize_provider_usage(
        {
            "input_tokens": 100,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 600,
            "output_tokens": 40,
        },
        provider="anthropic",
    )

    assert usage.input_tokens == 1_000
    assert usage.cached_input_tokens == 600
    assert usage.cache_write_input_tokens == 300
    assert usage.uncached_input_tokens == 100


def test_normalizes_opentelemetry_genai_usage_attributes() -> None:
    usage = normalize_otel_genai_attributes(
        {
            "gen_ai.provider.name": "anthropic",
            "gen_ai.usage.input_tokens": 1_000,
            "gen_ai.usage.cache_read.input_tokens": 600,
            "gen_ai.usage.cache_creation.input_tokens": 300,
            "gen_ai.usage.output_tokens": 40,
        }
    )

    assert usage.input_tokens == 1_000
    assert usage.cached_input_tokens == 600
    assert usage.cache_write_input_tokens == 300
    assert usage.uncached_input_tokens == 100
    assert usage.source == "otel:anthropic"


def test_cost_model_prices_categories_separately() -> None:
    usage = normalize_provider_usage(
        {
            "input_tokens": 1_000,
            "cached_input_tokens": 800,
            "output_tokens": 100,
            "reasoning_tokens": 60,
        }
    )
    cost = calculate_usage_cost(
        usage,
        PricingSnapshot(
            uncached_input_per_million_usd=10,
            cached_input_per_million_usd=1,
            output_per_million_usd=20,
            reasoning_per_million_usd=30,
        ),
    )

    assert cost is not None
    assert cost.uncached_input_usd == pytest.approx(0.002)
    assert cost.cached_input_usd == pytest.approx(0.0008)
    assert cost.output_usd == pytest.approx(0.0008)
    assert cost.reasoning_usd == pytest.approx(0.0018)
