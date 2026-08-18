"""Provider-neutral token, cache, behavior, and latency normalization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from contextlens.experiments import AgentOutcome


@dataclass(frozen=True, slots=True)
class NormalizedUsage:
    """Comparable usage categories without pretending all tokens cost the same."""

    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    uncached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    source: str = "unknown"
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "uncached_input_tokens": self.uncached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "source": self.source,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class PricingSnapshot:
    """Explicit historical prices in USD per one million category tokens."""

    uncached_input_per_million_usd: float
    cached_input_per_million_usd: float
    output_per_million_usd: float
    reasoning_per_million_usd: float | None = None
    cache_write_input_per_million_usd: float | None = None
    provider: str = "custom"
    model: str = "custom"
    effective_date: str | None = None

    def __post_init__(self) -> None:
        values = (
            self.uncached_input_per_million_usd,
            self.cached_input_per_million_usd,
            self.output_per_million_usd,
            self.reasoning_per_million_usd,
            self.cache_write_input_per_million_usd,
        )
        if any(value is not None and value < 0 for value in values):
            raise ValueError("pricing values cannot be negative")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PricingSnapshot:
        return cls(
            uncached_input_per_million_usd=float(
                value["uncached_input_per_million_usd"]
            ),
            cached_input_per_million_usd=float(value["cached_input_per_million_usd"]),
            output_per_million_usd=float(value["output_per_million_usd"]),
            reasoning_per_million_usd=_optional_float(
                value.get("reasoning_per_million_usd")
            ),
            cache_write_input_per_million_usd=_optional_float(
                value.get("cache_write_input_per_million_usd")
            ),
            provider=str(value.get("provider", "custom")),
            model=str(value.get("model", "custom")),
            effective_date=(
                str(value["effective_date"])
                if value.get("effective_date") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class UsageCostBreakdown:
    """Cost by token category from an explicit pricing snapshot."""

    uncached_input_usd: float
    cached_input_usd: float
    cache_write_input_usd: float
    output_usd: float
    reasoning_usd: float

    @property
    def total_usd(self) -> float:
        return (
            self.uncached_input_usd
            + self.cached_input_usd
            + self.cache_write_input_usd
            + self.output_usd
            + self.reasoning_usd
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "uncached_input_usd": self.uncached_input_usd,
            "cached_input_usd": self.cached_input_usd,
            "cache_write_input_usd": self.cache_write_input_usd,
            "output_usd": self.output_usd,
            "reasoning_usd": self.reasoning_usd,
            "total_usd": self.total_usd,
        }


def normalize_provider_usage(
    value: Mapping[str, Any],
    *,
    provider: str | None = None,
) -> NormalizedUsage:
    """Normalize common OpenAI, Anthropic, and generic usage objects.

    Unknown or missing categories stay ``None``. ContextLens never substitutes
    an injected-context estimate for provider-reported consumption here.
    """

    source = (provider or str(value.get("provider", "generic"))).casefold()
    usage = _usage_mapping(value)
    raw_input = _int_from(usage, "input_tokens", "input")
    raw_output = _int_from(usage, "output_tokens", "output")
    cached = _int_from(usage, "cached_input_tokens", "cached_tokens", "cached")
    cache_read = _int_from(usage, "cache_read_input_tokens")
    cache_write = _int_from(usage, "cache_creation_input_tokens")
    input_details = usage.get("input_tokens_details")
    if cached is None and isinstance(input_details, Mapping):
        cached = _int_from(input_details, "cached_tokens")
    output_details = usage.get("output_tokens_details")
    reasoning = _int_from(usage, "reasoning_tokens")
    if reasoning is None and isinstance(output_details, Mapping):
        reasoning = _int_from(output_details, "reasoning_tokens")

    warnings: list[str] = []
    uncached: int | None
    total_input: int | None
    if cache_read is not None or cache_write is not None:
        cached = cache_read if cache_read is not None else cached
        uncached = raw_input
        total_input = (
            (uncached or 0) + (cache_write or 0) + (cached or 0)
            if raw_input is not None or cached is not None or cache_write is not None
            else None
        )
    else:
        total_input = raw_input
        uncached = (
            max(0, total_input - cached)
            if total_input is not None and cached is not None
            else total_input
        )
        if total_input is not None and cached is not None and cached > total_input:
            warnings.append(
                "cached input exceeded total input; uncached input clamped to zero"
            )

    visible_output = raw_output
    if raw_output is not None and reasoning is not None:
        if reasoning <= raw_output:
            visible_output = raw_output - reasoning
        else:
            warnings.append(
                "reasoning tokens exceeded output tokens; output left unchanged"
            )
    return NormalizedUsage(
        input_tokens=total_input,
        cached_input_tokens=cached,
        uncached_input_tokens=uncached,
        cache_write_input_tokens=cache_write,
        output_tokens=visible_output,
        reasoning_tokens=reasoning,
        source=source,
        warnings=tuple(warnings),
    )


def normalize_otel_genai_attributes(
    attributes: Mapping[str, Any],
) -> NormalizedUsage:
    """Normalize OpenTelemetry GenAI agent-span usage attributes.

    The upstream convention defines total input as inclusive of cache reads and
    cache creation, so the returned categories are made mutually exclusive for
    cost accounting.
    """

    total_input = _int_from(attributes, "gen_ai.usage.input_tokens")
    cached = _int_from(attributes, "gen_ai.usage.cache_read.input_tokens")
    cache_write = _int_from(
        attributes,
        "gen_ai.usage.cache_creation.input_tokens",
    )
    output = _int_from(attributes, "gen_ai.usage.output_tokens")
    warnings: list[str] = []
    uncached: int | None = None
    if total_input is not None:
        classified = (cached or 0) + (cache_write or 0)
        if classified > total_input:
            warnings.append(
                "OpenTelemetry cache categories exceeded total input; "
                "uncached input clamped to zero"
            )
        uncached = max(0, total_input - classified)
    provider = attributes.get("gen_ai.provider.name")
    source = f"otel:{provider}" if isinstance(provider, str) else "otel:gen_ai"
    return NormalizedUsage(
        input_tokens=total_input,
        cached_input_tokens=cached,
        uncached_input_tokens=uncached,
        cache_write_input_tokens=cache_write,
        output_tokens=output,
        source=source,
        warnings=tuple(warnings),
    )


def usage_from_outcome(outcome: AgentOutcome | None) -> NormalizedUsage:
    """Normalize an existing replay outcome and its provider metadata."""

    if outcome is None:
        return NormalizedUsage(source="missing_outcome")
    provider_usage = outcome.metadata.get("provider_usage")
    value: dict[str, Any] = (
        dict(provider_usage) if isinstance(provider_usage, Mapping) else {}
    )
    if outcome.input_tokens is not None:
        value["input_tokens"] = outcome.input_tokens
    if outcome.cached_input_tokens is not None:
        value["cached_input_tokens"] = outcome.cached_input_tokens
    if outcome.output_tokens is not None:
        value["output_tokens"] = outcome.output_tokens
    for key in (
        "reasoning_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        if key in outcome.metadata:
            value[key] = outcome.metadata[key]
    provider = (
        str(outcome.metadata["provider"])
        if outcome.metadata.get("provider") is not None
        else None
    )
    return normalize_provider_usage(value, provider=provider)


def calculate_usage_cost(
    usage: NormalizedUsage,
    pricing: PricingSnapshot,
) -> UsageCostBreakdown | None:
    """Price normalized usage only when every observed category has a rate."""

    if usage.uncached_input_tokens is None or usage.output_tokens is None:
        return None
    if usage.cached_input_tokens is not None:
        cached_tokens = usage.cached_input_tokens
    else:
        cached_tokens = 0
    if usage.reasoning_tokens is not None and pricing.reasoning_per_million_usd is None:
        return None
    if (
        usage.cache_write_input_tokens is not None
        and pricing.cache_write_input_per_million_usd is None
    ):
        return None
    return UsageCostBreakdown(
        uncached_input_usd=(
            usage.uncached_input_tokens
            * pricing.uncached_input_per_million_usd
            / 1_000_000
        ),
        cached_input_usd=(
            cached_tokens * pricing.cached_input_per_million_usd / 1_000_000
        ),
        cache_write_input_usd=(
            (usage.cache_write_input_tokens or 0)
            * (pricing.cache_write_input_per_million_usd or 0)
            / 1_000_000
        ),
        output_usd=(usage.output_tokens * pricing.output_per_million_usd / 1_000_000),
        reasoning_usd=(
            (usage.reasoning_tokens or 0)
            * (pricing.reasoning_per_million_usd or 0)
            / 1_000_000
        ),
    )


def _usage_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("usage", "token_usage"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            return nested
    return value


def _int_from(value: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            return item
    return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("pricing values must be numeric")
    return float(value)
