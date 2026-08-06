"""Explicit, reproducible context mutations for replay experiments."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol

from contextlens.trace.model import ContextSource, SourceKind


class MutationOperation(StrEnum):
    """The intentionally small MVP mutation language."""

    REMOVE = "remove"
    SUMMARIZE = "summarize"
    LAZY_LOAD = "lazy_load"
    SCOPE = "scope"


@dataclass(frozen=True, slots=True)
class ContextMutation:
    """One intervention applied to one context item."""

    operation: MutationOperation
    context_item_id: str
    target_tokens: int | None = None
    target_agent_ids: tuple[str, ...] = ()
    target_phases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.context_item_id:
            raise ValueError("context_item_id cannot be empty")
        if self.operation is MutationOperation.SUMMARIZE:
            if self.target_tokens is None or self.target_tokens < 1:
                raise ValueError("summarize requires positive target_tokens")
        elif self.target_tokens is not None:
            raise ValueError("target_tokens is only valid for summarize")
        if self.operation is MutationOperation.SCOPE:
            if not self.target_agent_ids and not self.target_phases:
                raise ValueError("scope requires an agent or phase target")
        elif self.target_agent_ids or self.target_phases:
            raise ValueError("scope targets are only valid for scope")
        object.__setattr__(self, "target_agent_ids", tuple(self.target_agent_ids))
        object.__setattr__(self, "target_phases", tuple(self.target_phases))

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "operation": self.operation.value,
            "context_item_id": self.context_item_id,
        }
        if self.target_tokens is not None:
            value["target_tokens"] = self.target_tokens
        if self.target_agent_ids:
            value["target_agent_ids"] = list(self.target_agent_ids)
        if self.target_phases:
            value["target_phases"] = list(self.target_phases)
        return value


@dataclass(frozen=True, slots=True)
class SummaryResult:
    """Generated replacement plus the dependencies needed to reproduce it."""

    content: str
    prompt: str
    provider: str
    model: str
    model_version: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content.strip() or not self.prompt.strip():
            raise ValueError("summary content and prompt cannot be empty")
        if not self.provider or not self.model:
            raise ValueError("summary provider and model cannot be empty")


class Summarizer(Protocol):
    """Explicit provider chosen by the user for a summary mutation."""

    @property
    def summarizer_id(self) -> str:
        """Stable provider/model/configuration identity."""

    def summarize(
        self,
        source: ContextSource,
        target_tokens: int,
    ) -> SummaryResult:
        """Generate a recorded experimental replacement."""


@dataclass(frozen=True, slots=True)
class MutationApplication:
    """Initial and retrievable context after applying mutations."""

    context: tuple[ContextSource, ...]
    lazy_context: tuple[ContextSource, ...] = ()
    generated_context: tuple[ContextSource, ...] = ()


def apply_mutations(
    context: tuple[ContextSource, ...],
    mutations: tuple[ContextMutation, ...],
    *,
    summarizer: Summarizer | None = None,
    agent_id: str | None = None,
    phase: str | None = None,
) -> MutationApplication:
    """Apply mutations without changing the original context objects."""

    by_id = {source.source_id: source for source in context}
    if len(by_id) != len(context):
        raise ValueError("context source IDs must be unique")
    unknown = {
        mutation.context_item_id
        for mutation in mutations
        if mutation.context_item_id not in by_id
    }
    if unknown:
        raise ValueError(f"mutations reference unknown source IDs: {sorted(unknown)}")
    seen: set[str] = set()
    for mutation in mutations:
        if mutation.context_item_id in seen:
            raise ValueError("only one mutation per context item is supported")
        seen.add(mutation.context_item_id)

    mutation_by_id = {item.context_item_id: item for item in mutations}
    selected: list[ContextSource] = []
    lazy: list[ContextSource] = []
    generated: list[ContextSource] = []
    for source in context:
        selected_mutation = mutation_by_id.get(source.source_id)
        if selected_mutation is None:
            selected.append(source)
            continue
        if selected_mutation.operation is MutationOperation.REMOVE:
            continue
        if selected_mutation.operation is MutationOperation.LAZY_LOAD:
            lazy.append(source)
            continue
        if selected_mutation.operation is MutationOperation.SCOPE:
            agent_match = (
                not selected_mutation.target_agent_ids
                or agent_id in selected_mutation.target_agent_ids
            )
            phase_match = (
                not selected_mutation.target_phases
                or phase in selected_mutation.target_phases
            )
            if agent_match and phase_match:
                selected.append(
                    replace(
                        source,
                        target_agent_id=agent_id,
                        target_phase=phase,
                    )
                )
            continue
        assert selected_mutation.operation is MutationOperation.SUMMARIZE
        if summarizer is None:
            raise ValueError("summarize mutation requires an explicit summarizer")
        assert selected_mutation.target_tokens is not None
        result = summarizer.summarize(source, selected_mutation.target_tokens)
        digest_input = "\0".join(
            (
                source.source_id,
                source.content_hash or "",
                result.content,
                result.prompt,
                result.provider,
                result.model,
            )
        )
        summary_id = "summary-" + hashlib.sha256(
            digest_input.encode("utf-8")
        ).hexdigest()[:32]
        summary = ContextSource(
            source_id=summary_id,
            kind=SourceKind.GENERATED_SUMMARY,
            name=f"Summary of {source.name}",
            source_uri=source.source_uri,
            content=result.content,
            token_count=selected_mutation.target_tokens,
            token_count_method=f"summary_target:{result.model}",
            inserted_at_step=source.inserted_at_step,
            insertion_position=source.insertion_position,
            target_agent_id=source.target_agent_id,
            target_phase=source.target_phase,
            provenance={
                "source_item_id": source.source_id,
                "source_content_hash": source.content_hash,
                "summarization_prompt": result.prompt,
                "summarization_provider": result.provider,
                "summarization_model": result.model,
                "summarization_model_version": result.model_version,
                "summarizer_id": summarizer.summarizer_id,
                **result.metadata,
            },
            tags=tuple(dict.fromkeys((*source.tags, "experimental_summary"))),
        )
        selected.append(summary)
        generated.append(summary)
    return MutationApplication(
        context=tuple(selected),
        lazy_context=tuple(lazy),
        generated_context=tuple(generated),
    )
