"""Apply verified context policies before an agent request."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from contextlens.policy import ContextPolicy, PolicyRule, PolicyStrategy
from contextlens.trace import ContextSource, SourceKind


@dataclass(frozen=True, slots=True)
class ContextDecision:
    """Auditable action taken for one context source."""

    source_id: str
    name: str
    action: str
    before_tokens: int
    after_tokens: int
    policy_rule: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "action": self.action,
            "before_tokens": self.before_tokens,
            "after_tokens": self.after_tokens,
            "tokens_saved": self.before_tokens - self.after_tokens,
            "policy_rule": self.policy_rule,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class AppliedContext:
    """Prompt-ready context plus deferred content and savings evidence."""

    included: tuple[ContextSource, ...]
    lazy: tuple[ContextSource, ...]
    excluded_source_ids: tuple[str, ...]
    decisions: tuple[ContextDecision, ...]
    unmatched_policy_rules: tuple[str, ...]
    uncovered_source_ids: tuple[str, ...]
    estimated_source_ids: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def before_tokens(self) -> int:
        return sum(item.before_tokens for item in self.decisions)

    @property
    def after_tokens(self) -> int:
        return sum(item.after_tokens for item in self.decisions)

    @property
    def saved_tokens(self) -> int:
        return self.before_tokens - self.after_tokens

    @property
    def reduction_fraction(self) -> float:
        return self.saved_tokens / self.before_tokens if self.before_tokens else 0.0

    def savings_dict(self) -> dict[str, Any]:
        return {
            "before_tokens": self.before_tokens,
            "after_tokens": self.after_tokens,
            "saved_tokens": self.saved_tokens,
            "reduction_fraction": self.reduction_fraction,
            "included_sources": len(self.included),
            "lazy_sources": len(self.lazy),
            "excluded_sources": len(self.excluded_source_ids),
            "token_count_quality": (
                "estimated" if self.estimated_source_ids else "recorded"
            ),
            "estimated_source_count": len(self.estimated_source_ids),
        }

    def prompt_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "context": [source.to_dict() for source in self.included],
            "savings": self.savings_dict(),
            "warnings": list(self.warnings),
        }

    def lazy_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "context": [source.to_dict() for source in self.lazy],
        }

    def audit_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "savings": self.savings_dict(),
            "decisions": [item.to_dict() for item in self.decisions],
            "unmatched_policy_rules": list(self.unmatched_policy_rules),
            "uncovered_source_ids": list(self.uncovered_source_ids),
            "estimated_source_ids": list(self.estimated_source_ids),
            "warnings": list(self.warnings),
        }


def apply_context_policy(
    context: tuple[ContextSource, ...],
    policy: ContextPolicy,
    *,
    agent_id: str | None = None,
    phase: str | None = None,
    strict: bool = False,
) -> AppliedContext:
    """Apply a policy conservatively; sources without a rule remain included."""

    if not context:
        raise ValueError("context cannot be empty")
    by_source: dict[str, tuple[str, PolicyRule]] = {}
    matched_rules: set[str] = set()
    for rule_name, rule in policy.context.items():
        source_id = rule.parameters.get("source_id")
        matches = [
            source
            for source in context
            if (isinstance(source_id, str) and source.source_id == source_id)
            or (source_id is None and source.name in rule.sources)
        ]
        if matches:
            matched_rules.add(rule_name)
        for source in matches:
            if source.source_id in by_source:
                previous = by_source[source.source_id][0]
                raise ValueError(
                    f"context source {source.source_id!r} matches policy rules "
                    f"{previous!r} and {rule_name!r}"
                )
            by_source[source.source_id] = (rule_name, rule)

    unmatched = tuple(sorted(set(policy.context) - matched_rules))
    if strict and unmatched:
        raise ValueError(f"policy rules matched no context source: {unmatched}")

    included: list[ContextSource] = []
    lazy: list[ContextSource] = []
    excluded: list[str] = []
    decisions: list[ContextDecision] = []
    warnings: list[str] = []
    uncovered: list[str] = []
    estimated: list[str] = [
        source.source_id for source in context if source.token_count is None
    ]
    for source in context:
        before = _tokens(source)
        matched = by_source.get(source.source_id)
        if matched is None:
            uncovered.append(source.source_id)
            included.append(source)
            decisions.append(
                ContextDecision(
                    source.source_id,
                    source.name,
                    "include",
                    before,
                    before,
                    None,
                    "no policy rule matched; kept by default",
                )
            )
            continue
        rule_name, rule = matched
        expected_hash = rule.parameters.get("content_hash")
        if isinstance(expected_hash, str) and expected_hash != source.content_hash:
            included.append(source)
            warnings.append(
                f"{source.name}: content changed since policy verification; "
                "kept original"
            )
            decisions.append(
                ContextDecision(
                    source.source_id,
                    source.name,
                    "include",
                    before,
                    before,
                    rule_name,
                    "content hash changed; kept fail-closed",
                )
            )
            continue
        if rule.strategy in {
            PolicyStrategy.ALWAYS_INCLUDE,
            PolicyStrategy.NEEDS_MORE_EVIDENCE,
        }:
            included.append(source)
            reason = (
                "policy requires inclusion"
                if rule.strategy is PolicyStrategy.ALWAYS_INCLUDE
                else "evidence is insufficient; kept fail-closed"
            )
            decisions.append(
                ContextDecision(
                    source.source_id,
                    source.name,
                    "include",
                    before,
                    before,
                    rule_name,
                    reason,
                )
            )
        elif rule.strategy is PolicyStrategy.EXCLUDE:
            excluded.append(source.source_id)
            decisions.append(
                ContextDecision(
                    source.source_id,
                    source.name,
                    "exclude",
                    before,
                    0,
                    rule_name,
                    "verified policy excludes this source",
                )
            )
        elif rule.strategy is PolicyStrategy.LAZY_LOAD:
            lazy.append(source)
            decisions.append(
                ContextDecision(
                    source.source_id,
                    source.name,
                    "lazy_load",
                    before,
                    0,
                    rule_name,
                    "removed from the initial prompt and retained for retrieval",
                )
            )
        elif rule.strategy is PolicyStrategy.SUMMARIZE:
            summary = rule.parameters.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                included.append(source)
                warnings.append(
                    f"{source.name}: summary text unavailable; kept original"
                )
                decisions.append(
                    ContextDecision(
                        source.source_id,
                        source.name,
                        "include",
                        before,
                        before,
                        rule_name,
                        "summary text unavailable; kept fail-closed",
                    )
                )
            else:
                summarized = replace(
                    source,
                    kind=SourceKind.GENERATED_SUMMARY,
                    content=summary,
                    content_ref=None,
                    content_hash=None,
                    token_count=_estimate(summary),
                    token_count_method="estimated_utf8_bytes_div_4",
                    provenance={
                        **dict(source.provenance),
                        "summarized_from": source.source_id,
                    },
                )
                included.append(summarized)
                if source.source_id not in estimated:
                    estimated.append(source.source_id)
                decisions.append(
                    ContextDecision(
                        source.source_id,
                        source.name,
                        "summarize",
                        before,
                        _tokens(summarized),
                        rule_name,
                        "replaced with a policy-provided verified summary",
                    )
                )
        else:
            _apply_scope(
                source,
                rule_name,
                rule,
                before,
                agent_id,
                phase,
                included,
                excluded,
                decisions,
                warnings,
            )
    if unmatched:
        warnings.append(f"{len(unmatched)} policy rule(s) matched no source")
    if strict and uncovered:
        raise ValueError(f"context sources matched no policy rule: {tuple(uncovered)}")
    if estimated:
        warnings.append(f"token counts were estimated for {len(estimated)} source(s)")
    return AppliedContext(
        included=tuple(included),
        lazy=tuple(lazy),
        excluded_source_ids=tuple(excluded),
        decisions=tuple(decisions),
        unmatched_policy_rules=unmatched,
        uncovered_source_ids=tuple(uncovered),
        estimated_source_ids=tuple(estimated),
        warnings=tuple(warnings),
    )


def _apply_scope(
    source: ContextSource,
    rule_name: str,
    rule: PolicyRule,
    before: int,
    agent_id: str | None,
    phase: str | None,
    included: list[ContextSource],
    excluded: list[str],
    decisions: list[ContextDecision],
    warnings: list[str],
) -> None:
    missing_dimension = (bool(rule.target_agent_ids) and agent_id is None) or (
        bool(rule.target_phases) and phase is None
    )
    if missing_dimension:
        included.append(source)
        warnings.append(f"{source.name}: runtime scope unavailable; kept original")
        decisions.append(
            ContextDecision(
                source.source_id,
                source.name,
                "include",
                before,
                before,
                rule_name,
                "runtime scope unavailable; kept fail-closed",
            )
        )
        return
    matches = (not rule.target_agent_ids or agent_id in rule.target_agent_ids) and (
        not rule.target_phases or phase in rule.target_phases
    )
    if matches:
        included.append(source)
        decisions.append(
            ContextDecision(
                source.source_id,
                source.name,
                "include",
                before,
                before,
                rule_name,
                "runtime agent and phase match the policy scope",
            )
        )
    else:
        excluded.append(source.source_id)
        decisions.append(
            ContextDecision(
                source.source_id,
                source.name,
                "scope_exclude",
                before,
                0,
                rule_name,
                "source is outside the active agent or phase scope",
            )
        )


def _tokens(source: ContextSource) -> int:
    if source.token_count is not None:
        return source.token_count
    return _estimate(source.content or "")


def _estimate(content: str) -> int:
    return math.ceil(len(content.encode("utf-8")) / 4)
