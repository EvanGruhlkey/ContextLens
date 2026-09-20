"""Iterative observe, retain, filter, and decide controller session."""

from __future__ import annotations

from dataclasses import dataclass

from contextlens.action_controller import ActionController, ActionDecision
from contextlens.action_models import CandidateAction
from contextlens.observations import Observation, ObservationStore
from contextlens.retention import RetentionController, RetentionDecision
from contextlens.tool_filter import ToolFilter, ToolFilterDecision


@dataclass(frozen=True, slots=True)
class ControlStep:
    step: int
    action: ActionDecision
    retention: RetentionDecision
    tools: ToolFilterDecision


class ControlSession:
    """Own task state across repeated decisions without executing capabilities."""

    def __init__(
        self,
        *,
        task: str,
        store: ObservationStore,
        actions: ActionController,
        retention: RetentionController,
        tools: ToolFilter,
        repository_revision: str | None = None,
    ) -> None:
        if not task.strip():
            raise ValueError("task must be nonempty")
        self.task = task
        self.store = store
        self.actions = actions
        self.retention = retention
        self.tools = tools
        self.repository_revision = repository_revision
        self.step = 0

    def observe(
        self,
        *,
        kind: str,
        summary: str,
        content: str,
        source: str | None = None,
        pinned: bool = False,
    ) -> Observation:
        self.store.advance()
        return self.store.add(
            kind=kind,
            summary=summary,
            content=content,
            source=source,
            pinned=pinned,
        )

    def next(self, *, focus: str, candidates: list[CandidateAction]) -> ControlStep:
        retention = self.retention.evaluate(
            task=self.task, focus=focus, store=self.store
        )
        tools = self.tools.filter(task=self.task, focus=focus, candidates=candidates)
        offered = list(tools.candidates)
        if len(offered) < 2:
            offered = candidates
        bounded_observations = self.store.bounded_descriptors(20)
        action = self.actions.choose_next_action(
            task=self.task,
            focus=focus,
            observations=bounded_observations,
            candidates=offered,
            repository_revision=self.repository_revision,
        )
        self.step += 1
        return ControlStep(self.step, action, retention, tools)
