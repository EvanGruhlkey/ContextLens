"""Deterministic signals derived from one completed agent run."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from contextlens.profiler.adapters import ContentSimilarity, ModelInternalsAdapter
from contextlens.profiler.model import (
    EvidenceLevel,
    ProfileReason,
    RunObservation,
    SourceProfile,
    UsageLabel,
    UsageSignal,
)
from contextlens.trace.artifacts import ArtifactStore
from contextlens.trace.model import ContextEvent

_WORD = re.compile(r"[a-zA-Z0-9_./:-]+")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?", re.MULTILINE)
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class ProfileReport:
    """Profiles and aggregate token totals for one request."""

    request_id: str
    profiles: tuple[SourceProfile, ...]

    @property
    def total_tokens(self) -> int:
        return sum(profile.token_count for profile in self.profiles)

    def tokens_by_label(self) -> dict[str, int]:
        totals: Counter[str] = Counter()
        for profile in self.profiles:
            totals[profile.label.value] += profile.token_count
        return dict(totals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "evidence_level": EvidenceLevel.OBSERVED.value,
            "causal": False,
            "total_tokens": self.total_tokens,
            "tokens_by_label": self.tokens_by_label(),
            "sources": [profile.to_dict() for profile in self.profiles],
        }


class ContextProfiler:
    """Profile apparent context utilization without invoking a model."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore | None = None,
        overlap_threshold: float = 0.2,
        duplicate_threshold: float = 0.9,
        content_similarity: ContentSimilarity | None = None,
        model_internals: Sequence[ModelInternalsAdapter] = (),
    ) -> None:
        if not 0 <= overlap_threshold <= 1:
            raise ValueError("overlap_threshold must be between 0 and 1")
        if not 0 <= duplicate_threshold <= 1:
            raise ValueError("duplicate_threshold must be between 0 and 1")
        self.artifact_store = artifact_store
        self.overlap_threshold = overlap_threshold
        self.duplicate_threshold = duplicate_threshold
        self.content_similarity = content_similarity
        self.model_internals = tuple(model_internals)

    def profile(
        self,
        events: Sequence[ContextEvent],
        observation: RunObservation,
        *,
        now: datetime | None = None,
    ) -> ProfileReport:
        if not events:
            raise ValueError("at least one context event is required")
        request_ids = {event.request_id for event in events}
        if len(request_ids) != 1:
            raise ValueError("events must belong to exactly one request")
        ordered = sorted(events, key=lambda event: event.sequence)
        expected = list(range(len(ordered)))
        if [event.sequence for event in ordered] != expected:
            raise ValueError("events must have contiguous sequences starting at zero")

        contents = [self._content(event) for event in ordered]
        normalized = [
            _normalize(content) if content is not None else None
            for content in contents
        ]
        duplicates = self._duplicates(
            normalized,
            tuple(event.source.source_id for event in ordered),
        )
        output_tokens = _meaningful_tokens(observation.output_text)
        output_spans = tuple(
            span.strip()
            for span in _SENTENCE.findall(observation.output_text)
            if span.strip()
        )
        current_time = now or datetime.now(UTC)

        raw_profiles = tuple(
            self._profile_source(
                event=event,
                content=contents[index],
                normalized_content=normalized[index],
                duplicated_by=duplicates[index],
                position=index / (len(ordered) - 1) if len(ordered) > 1 else 0.0,
                observation=observation,
                output_tokens=output_tokens,
                output_spans=output_spans,
                now=current_time,
            )
            for index, event in enumerate(ordered)
        )
        maximum_tokens = max(profile.token_count for profile in raw_profiles) or 1
        profiles = tuple(
            self._score_profile(
                profile,
                event=ordered[index],
                content=contents[index] or "",
                observation=observation,
                maximum_tokens=maximum_tokens,
            )
            for index, profile in enumerate(raw_profiles)
        )
        return ProfileReport(request_id=ordered[0].request_id, profiles=profiles)

    def _score_profile(
        self,
        profile: SourceProfile,
        *,
        event: ContextEvent,
        content: str,
        observation: RunObservation,
        maximum_tokens: int,
    ) -> SourceProfile:
        source_tokens = _meaningful_tokens(content)
        task_tokens = _meaningful_tokens(observation.task_text)
        changed_tokens = _meaningful_tokens(" ".join(observation.changed_files))
        task_relevance = _overlap(source_tokens, task_tokens) if task_tokens else 0.0
        change_relevance = (
            _overlap(source_tokens, changed_tokens) if changed_tokens else 0.0
        )
        relevance = min(
            1.0,
            max(profile.output_overlap or 0.0, task_relevance, change_relevance),
        )
        usage = {
            UsageLabel.USED: 1.0,
            UsageLabel.DUPLICATED: 0.5,
            UsageLabel.UNCERTAIN: 0.25,
            UsageLabel.UNUSED: 0.0,
        }[profile.label]
        redundancy = min(1.0, len(profile.duplicated_by) / 2)
        contradiction = (
            1.0 if event.source.provenance.get("contradicts") else 0.0
        )
        staleness = (
            min(1.0, profile.age_seconds / (365 * 24 * 3600))
            if profile.age_seconds is not None
            else 0.0
        )
        token_cost = min(1.0, profile.token_count / maximum_tokens)
        uncertainty = {
            UsageLabel.USED: 0.35,
            UsageLabel.UNUSED: 0.65,
            UsageLabel.DUPLICATED: 0.8,
            UsageLabel.UNCERTAIN: 1.0,
        }[profile.label]
        meaningful_effect = max(0.25, relevance, redundancy, contradiction)
        priority = token_cost * uncertainty * meaningful_effect * 0.9
        reasons = [
            ProfileReason(
                "token_cost",
                "candidate token cost relative to the largest context item",
                {"tokens": profile.token_count, "normalized": token_cost},
            ),
            ProfileReason(
                "observed_usage",
                "deterministic usage signals from the baseline trajectory",
                {"label": profile.label.value, "score": usage},
            ),
        ]
        if relevance:
            reasons.append(
                ProfileReason(
                    "task_relevance",
                    "lexical overlap with task, output, or changed files",
                    {"score": relevance},
                )
            )
        if redundancy:
            reasons.append(
                ProfileReason(
                    "redundancy",
                    "similar content exists in another context item",
                    {"source_ids": list(profile.duplicated_by)},
                )
            )
        if contradiction:
            reasons.append(
                ProfileReason(
                    "declared_contradiction",
                    "source provenance declares a conflicting context item",
                    {"contradicts": event.source.provenance.get("contradicts")},
                )
            )
        search_text = " ".join(observation.searched_queries).casefold()
        if search_text and any(token in search_text for token in source_tokens):
            reasons.append(
                ProfileReason(
                    "searched_despite_context",
                    "the agent searched for terms already present in this item",
                )
            )
        return replace(
            profile,
            relevance_score=round(relevance, 6),
            observed_usage_score=usage,
            redundancy_score=round(redundancy, 6),
            contradiction_score=contradiction,
            staleness_score=round(staleness, 6),
            token_cost_score=round(token_cost, 6),
            experiment_priority=round(priority, 6),
            reasons=tuple(reasons),
        )

    def _profile_source(
        self,
        *,
        event: ContextEvent,
        content: str | None,
        normalized_content: str | None,
        duplicated_by: tuple[str, ...],
        position: float,
        observation: RunObservation,
        output_tokens: frozenset[str],
        output_spans: tuple[str, ...],
        now: datetime,
    ) -> SourceProfile:
        source = event.source
        token_count, token_method = _token_count(
            source.token_count,
            source.token_count_method,
            content,
        )
        signals: list[UsageSignal] = [
            UsageSignal("context_position", round(position, 6), "0=start, 1=end"),
            UsageSignal("token_count", token_count, token_method),
        ]

        direct = source.source_id in observation.accessed_source_ids
        if direct:
            signals.append(
                UsageSignal(
                    "direct_access",
                    True,
                    "the agent runtime recorded direct access to this source",
                )
            )

        reference_haystack = "\n".join(
            (
                observation.output_text,
                *observation.commands,
                *observation.tool_inputs,
                *observation.changed_files,
            )
        ).casefold()
        referenced = _source_referenced(
            source.name,
            source.provenance,
            reference_haystack,
        )
        if referenced:
            signals.append(
                UsageSignal(
                    "name_or_path_reference",
                    True,
                    "the source name or provenance path appeared in run output",
                )
            )

        source_tokens = _meaningful_tokens(content or "")
        overlap = _overlap(source_tokens, output_tokens) if source_tokens else None
        matched_spans = _matched_spans(
            source_tokens,
            output_spans,
            self.overlap_threshold,
        )
        if overlap is not None:
            signals.append(
                UsageSignal(
                    "output_token_overlap",
                    round(overlap, 6),
                    "fraction of meaningful source tokens found in final output",
                )
            )
        if duplicated_by:
            signals.append(
                UsageSignal(
                    "duplicate_source_count",
                    len(duplicated_by),
                    "similar content also appears in: " + ", ".join(duplicated_by),
                )
            )

        age = _age_seconds(source.provenance.get("created_at"), now)
        rank = _retrieval_rank(source.provenance.get("retrieval_rank"))
        if age is not None:
            signals.append(UsageSignal("age_seconds", age, "age at profiling time"))
        if rank is not None:
            signals.append(
                UsageSignal(
                    "retrieval_rank",
                    rank,
                    "recorded retrieval rank",
                )
            )
        for adapter in self.model_internals:
            signals.extend(adapter.signals(event, observation))

        if (
            direct
            or referenced
            or matched_spans
            or (overlap is not None and overlap >= self.overlap_threshold)
        ):
            label = UsageLabel.USED
        elif duplicated_by:
            label = UsageLabel.DUPLICATED
        elif normalized_content is None or len(source_tokens) < 3:
            label = UsageLabel.UNCERTAIN
        else:
            label = UsageLabel.UNUSED

        return SourceProfile(
            source_id=source.source_id,
            name=source.name,
            kind=source.kind.value,
            label=label,
            token_count=token_count,
            token_count_method=token_method,
            position=position,
            output_overlap=overlap,
            duplicated_by=duplicated_by,
            age_seconds=age,
            retrieval_rank=rank,
            matched_output_spans=matched_spans,
            signals=tuple(signals),
        )

    def _content(self, event: ContextEvent) -> str | None:
        source = event.source
        if source.content is not None:
            return source.content
        if source.content_ref is None or self.artifact_store is None:
            return None
        return self.artifact_store.get(source.content_ref).decode("utf-8")

    def _duplicates(
        self,
        normalized: Sequence[str | None],
        source_ids: Sequence[str],
    ) -> tuple[tuple[str, ...], ...]:
        matches: list[list[int]] = [[] for _ in normalized]
        for right in range(len(normalized)):
            right_text = normalized[right]
            if not right_text:
                continue
            for left in range(right):
                left_text = normalized[left]
                if not left_text:
                    continue
                if (
                    left_text == right_text
                    or self._similarity(left_text, right_text)
                    >= self.duplicate_threshold
                ):
                    matches[right].append(left)
                    matches[left].append(right)
        return tuple(
            tuple(source_ids[index] for index in indexes)
            for indexes in matches
        )

    def _similarity(self, left: str, right: str) -> float:
        lexical = _similarity(left, right)
        if self.content_similarity is None:
            return lexical
        semantic = self.content_similarity.score(left, right)
        if not 0 <= semantic <= 1:
            raise ValueError("content similarity scores must be between 0 and 1")
        return max(lexical, semantic)


def _normalize(content: str) -> str:
    return " ".join(_WORD.findall(content.casefold()))


def _meaningful_tokens(content: str) -> frozenset[str]:
    return frozenset(
        token
        for token in _WORD.findall(content.casefold())
        if len(token) > 1 and token not in _STOP_WORDS
    )


def _overlap(source_tokens: frozenset[str], output_tokens: frozenset[str]) -> float:
    if not source_tokens:
        return 0.0
    return len(source_tokens & output_tokens) / len(source_tokens)


def _matched_spans(
    source_tokens: frozenset[str],
    output_spans: Sequence[str],
    threshold: float,
) -> tuple[str, ...]:
    if len(source_tokens) < 3:
        return ()
    matched: list[str] = []
    for span in output_spans:
        span_tokens = _meaningful_tokens(span)
        if len(span_tokens) >= 3 and _overlap(span_tokens, source_tokens) >= threshold:
            matched.append(span)
    return tuple(matched[:5])


def _similarity(left: str, right: str) -> float:
    if min(len(left), len(right)) < 20:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _token_count(
    recorded_count: int | None,
    recorded_method: str | None,
    content: str | None,
) -> tuple[int, str]:
    if recorded_count is not None:
        assert recorded_method is not None
        return recorded_count, recorded_method
    if content is None:
        return 0, "unavailable"
    # A transparent fallback, not a provider tokenizer.
    estimate = math.ceil(len(content.encode("utf-8")) / 4)
    return estimate, "estimated_utf8_bytes_div_4"


def _source_referenced(
    name: str,
    provenance: Any,
    haystack: str,
) -> bool:
    candidates = [name]
    if hasattr(provenance, "get"):
        for key in ("path", "uri", "url"):
            value = provenance.get(key)
            if isinstance(value, str):
                candidates.append(value)
    return any(
        len(candidate.strip()) >= 3 and candidate.casefold() in haystack
        for candidate in candidates
    )


def _age_seconds(value: Any, now: datetime) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return max(0.0, (now - created).total_seconds())


def _retrieval_rank(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
