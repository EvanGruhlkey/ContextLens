"""Validated, machine-readable context-loading policies."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from contextlens.experiments.mutations import ContextMutation, MutationOperation
from contextlens.optimization.model import VerifiedConfiguration
from contextlens.reports.model import Report
from contextlens.trace.model import ContextSource


class PolicyStrategy(StrEnum):
    """Executable context-loading strategies."""

    ALWAYS_INCLUDE = "always_include"
    EXCLUDE = "exclude"
    SUMMARIZE = "summarize"
    LAZY_LOAD = "lazy_load"
    SCOPED = "scoped"
    NEEDS_MORE_EVIDENCE = "needs_more_evidence"


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """Policy for one named class of context."""

    sources: tuple[str, ...]
    strategy: PolicyStrategy
    max_tokens: int | None = None
    target_agent_ids: tuple[str, ...] = ()
    target_phases: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.sources or any(not source for source in self.sources):
            raise ValueError("policy rule requires nonempty sources")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if (
            self.strategy is PolicyStrategy.SCOPED
            and not self.target_agent_ids
            and not self.target_phases
        ):
            raise ValueError("scoped policy requires an agent or phase")
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "target_agent_ids", tuple(self.target_agent_ids))
        object.__setattr__(self, "target_phases", tuple(self.target_phases))
        object.__setattr__(
            self,
            "parameters",
            MappingProxyType(dict(self.parameters)),
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "sources": list(self.sources),
            "strategy": self.strategy.value,
        }
        if self.max_tokens is not None:
            value["max_tokens"] = self.max_tokens
        if self.target_agent_ids:
            value["target_agent_ids"] = list(self.target_agent_ids)
        if self.target_phases:
            value["target_phases"] = list(self.target_phases)
        value.update(self.parameters)
        return value


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Versioned policy validated before export or reuse."""

    context: Mapping[str, PolicyRule]
    objective: str = "balanced"
    version: int = 1

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ValueError("only context policy version 1 is supported")
        if self.objective not in {
            "quality",
            "cost_without_regression",
            "latency_without_regression",
            "balanced",
        }:
            raise ValueError(f"unsupported objective: {self.objective}")
        if not self.context:
            raise ValueError("policy requires at least one context rule")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in self.context):
            raise ValueError("policy rule keys must be lower_snake_case")
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "objective": self.objective,
            "context": {
                name: rule.to_dict() for name, rule in sorted(self.context.items())
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def to_yaml(self) -> str:
        lines = [
            f"version: {self.version}",
            f"objective: {self.objective}",
            "context:",
        ]
        for name, rule in sorted(self.context.items()):
            lines.append(f"  {name}:")
            lines.append("    sources:")
            lines.extend(f"      - {_scalar(source)}" for source in rule.sources)
            lines.append(f"    strategy: {rule.strategy.value}")
            if rule.max_tokens is not None:
                lines.append(f"    max_tokens: {rule.max_tokens}")
            if rule.target_agent_ids:
                lines.append("    target_agent_ids:")
                lines.extend(
                    f"      - {_scalar(value)}" for value in rule.target_agent_ids
                )
            if rule.target_phases:
                lines.append("    target_phases:")
                lines.extend(
                    f"      - {_scalar(value)}" for value in rule.target_phases
                )
            for key, value in sorted(rule.parameters.items()):
                lines.append(f"    {key}: {_scalar(value)}")
        return "\n".join(lines) + "\n"


def policy_from_report(
    report: Report,
    *,
    objective: str = "balanced",
) -> ContextPolicy:
    """Translate strongest report findings into conservative executable rules."""

    evidence_order = {"verified": 3, "target_model": 3, "predicted": 2, "observed": 1}
    selected: dict[str, Any] = {}
    for finding in report.findings:
        current = selected.get(finding.source_id)
        if current is None or evidence_order.get(
            finding.evidence_level, 0
        ) > evidence_order.get(current.evidence_level, 0):
            selected[finding.source_id] = finding
    rules: dict[str, PolicyRule] = {}
    used_keys: set[str] = set()
    for finding in selected.values():
        strategy = _strategy(finding.action, finding.verdict)
        key = _key(finding.kind or finding.name)
        base = key
        suffix = 2
        while key in used_keys:
            key = f"{base}_{suffix}"
            suffix += 1
        used_keys.add(key)
        rules[key] = PolicyRule(
            sources=(finding.name,),
            strategy=strategy,
            max_tokens=(
                max(1, finding.tokens // 4)
                if strategy is PolicyStrategy.SUMMARIZE and finding.tokens
                else None
            ),
        )
    return ContextPolicy(context=rules, objective=objective)


def policy_from_verified_configuration(
    context: tuple[ContextSource, ...],
    verified: VerifiedConfiguration,
    *,
    objective: str = "balanced",
) -> ContextPolicy:
    """Compile the optimizer's accepted combined candidate into a policy.

    Unlike :func:`policy_from_report`, this conversion is tied directly to the
    target-model verification result. A rejected candidate never yields
    exclusion rules.
    """

    source_id_sequence = tuple(source.source_id for source in context)
    source_ids = set(source_id_sequence)
    removed_ids = verified.candidate.removed_source_ids
    retained_ids = verified.candidate.retained_source_ids
    removed = set(removed_ids)
    retained = set(retained_ids)
    if len(source_ids) != len(source_id_sequence):
        raise ValueError("supplied context source IDs must be unique")
    if len(removed) != len(removed_ids) or len(retained) != len(retained_ids):
        raise ValueError("verified candidate source IDs must be unique")
    if removed & retained:
        raise ValueError("verified candidate removed and retained sources overlap")
    if source_ids != removed | retained:
        raise ValueError("verified candidate must partition the supplied context")
    removed = removed if verified.accepted else set()
    rules: dict[str, PolicyRule] = {}
    used_keys: set[str] = set()
    for source in context:
        key = _key(source.name)
        base = key
        suffix = 2
        while key in used_keys:
            key = f"{base}_{suffix}"
            suffix += 1
        used_keys.add(key)
        if not verified.accepted:
            strategy = PolicyStrategy.NEEDS_MORE_EVIDENCE
        elif source.source_id in removed:
            strategy = PolicyStrategy.EXCLUDE
        else:
            strategy = PolicyStrategy.ALWAYS_INCLUDE
        rules[key] = PolicyRule(
            sources=(source.name,),
            strategy=strategy,
            parameters={
                "source_id": source.source_id,
                "verification_run_id": verified.replay_result.run_id,
                "evidence_scope": verified.evidence_scope,
            },
        )
    return ContextPolicy(context=rules, objective=objective)


def mutations_from_policy(
    context: tuple[ContextSource, ...],
    policy: ContextPolicy,
) -> tuple[ContextMutation, ...]:
    """Compile executable policy rules for a future run's known sources."""

    by_name: dict[str, list[ContextSource]] = {}
    for source in context:
        by_name.setdefault(source.name, []).append(source)
    mutations: list[ContextMutation] = []
    mutated: set[str] = set()
    for rule in policy.context.values():
        for source_name in rule.sources:
            for source in by_name.get(source_name, ()):
                if source.source_id in mutated:
                    raise ValueError(
                        f"multiple policy rules match source {source.source_id!r}"
                    )
                mutation = _mutation_from_rule(source, rule)
                if mutation is not None:
                    mutations.append(mutation)
                    mutated.add(source.source_id)
    return tuple(mutations)


def _strategy(action: str | None, verdict: str) -> PolicyStrategy:
    actions = {
        "keep": PolicyStrategy.ALWAYS_INCLUDE,
        "retain": PolicyStrategy.ALWAYS_INCLUDE,
        "remove": PolicyStrategy.EXCLUDE,
        "summarize": PolicyStrategy.SUMMARIZE,
        "lazy_load": PolicyStrategy.LAZY_LOAD,
        "scope_to_agent": PolicyStrategy.NEEDS_MORE_EVIDENCE,
        "scope_to_phase": PolicyStrategy.NEEDS_MORE_EVIDENCE,
        "investigate": PolicyStrategy.NEEDS_MORE_EVIDENCE,
        "needs_more_evidence": PolicyStrategy.NEEDS_MORE_EVIDENCE,
    }
    if action in actions:
        return actions[action]
    if verdict == "helpful":
        return PolicyStrategy.ALWAYS_INCLUDE
    if verdict == "harmful":
        return PolicyStrategy.EXCLUDE
    return PolicyStrategy.NEEDS_MORE_EVIDENCE


def _mutation_from_rule(
    source: ContextSource,
    rule: PolicyRule,
) -> ContextMutation | None:
    if rule.strategy in {
        PolicyStrategy.ALWAYS_INCLUDE,
        PolicyStrategy.NEEDS_MORE_EVIDENCE,
    }:
        return None
    if rule.strategy is PolicyStrategy.EXCLUDE:
        return ContextMutation(MutationOperation.REMOVE, source.source_id)
    if rule.strategy is PolicyStrategy.LAZY_LOAD:
        return ContextMutation(MutationOperation.LAZY_LOAD, source.source_id)
    if rule.strategy is PolicyStrategy.SUMMARIZE:
        if rule.max_tokens is None:
            raise ValueError("summarize policy requires max_tokens")
        return ContextMutation(
            MutationOperation.SUMMARIZE,
            source.source_id,
            target_tokens=rule.max_tokens,
        )
    assert rule.strategy is PolicyStrategy.SCOPED
    return ContextMutation(
        MutationOperation.SCOPE,
        source.source_id,
        target_agent_ids=rule.target_agent_ids,
        target_phases=rule.target_phases,
    )


def _key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = "context_" + normalized
    return normalized


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)
