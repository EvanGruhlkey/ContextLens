"""Record a completed production replay as a ContextLens trace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from contextlens.experiments.model import (
    AgentSettings,
    ReplayResult,
    ReplayStatus,
    ReplayTask,
)
from contextlens.profiler.model import RunObservation
from contextlens.trace.model import (
    AgentStatus,
    AgentTrace,
    ContextSource,
    StepType,
    TokenUsage,
    TraceHeader,
    TraceStep,
)
from contextlens.trace.store import TraceReader, TraceWriter


@dataclass(frozen=True, slots=True)
class RecordedReplayTrace:
    """A durable replay trace plus the observation derived from its outcome."""

    path: Path
    trace_id: str
    request_id: str
    observation: RunObservation


def record_replay_trace(
    path: Path,
    *,
    task: ReplayTask,
    context: tuple[ContextSource, ...],
    settings: AgentSettings,
    result: ReplayResult,
    request_id: str = "model-request-0",
) -> RecordedReplayTrace:
    """Persist one genuine replay using the production trace schema."""

    selected = tuple(
        source for source in context if source.source_id in result.context_source_ids
    )
    if len(selected) != len(result.context_source_ids):
        raise ValueError("replay references context sources missing from the manifest")
    if path.exists():
        raise ValueError(f"trace already exists: {path}")
    outcome = result.outcome
    input_tokens = outcome.input_tokens if outcome and outcome.input_tokens else 0
    cached_tokens = (
        outcome.cached_input_tokens if outcome and outcome.cached_input_tokens else 0
    )
    output_tokens = outcome.output_tokens if outcome and outcome.output_tokens else 0
    status = {
        ReplayStatus.COMPLETED: AgentStatus.COMPLETED,
        ReplayStatus.CACHED: AgentStatus.COMPLETED,
        ReplayStatus.CANCELLED: AgentStatus.CANCELLED,
    }.get(result.status, AgentStatus.FAILED)
    header = TraceHeader(trace_id=result.run_id)
    trace = AgentTrace(
        trace_id=result.run_id,
        task=task.instruction,
        agent_type="contextlens-replay-worker",
        model_provider=settings.provider,
        model_name=settings.model,
        started_at=result.started_at or header.created_at,
        completed_at=result.ended_at,
        status=status,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_cached_tokens=cached_tokens,
        total_tool_calls=outcome.tool_calls if outcome else 0,
        total_runtime_ms=round(result.duration_seconds * 1_000),
        metadata={
            "run_id": result.run_id,
            "variant_id": result.variant_id,
            "workspace_id": result.workspace_id,
            "removed_source_ids": list(result.removed_source_ids),
            "error": result.error,
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    sequence = 0
    with TraceWriter(path, header=header) as writer:
        writer.set_trace(trace)
        for source in selected:
            writer.add(request_id, source)
        writer.add_step(
            TraceStep(
                trace_id=result.run_id,
                sequence=sequence,
                step_type=StepType.MODEL_REQUEST,
                input_context_item_ids=tuple(source.source_id for source in selected),
                token_usage=TokenUsage(
                    input=input_tokens,
                    cached=cached_tokens,
                ),
                content=(
                    str(outcome.metadata.get("rendered_prompt"))
                    if outcome and outcome.metadata.get("rendered_prompt")
                    else None
                ),
            )
        )
        sequence += 1
        if outcome is not None:
            for command in outcome.commands:
                writer.add_step(
                    TraceStep(
                        trace_id=result.run_id,
                        sequence=sequence,
                        step_type=StepType.TOOL_CALL,
                        tool_name="shell",
                        tool_input=command,
                    )
                )
                sequence += 1
            writer.add_step(
                TraceStep(
                    trace_id=result.run_id,
                    sequence=sequence,
                    step_type=StepType.MODEL_RESPONSE,
                    token_usage=TokenUsage(output=output_tokens),
                    content=outcome.output_text,
                    metadata={
                        "raw_model_response": outcome.metadata.get(
                            "raw_model_response"
                        ),
                        "raw_jsonl": outcome.metadata.get("raw_jsonl"),
                    },
                )
            )
            sequence += 1
            if outcome.test_results:
                writer.add_step(
                    TraceStep(
                        trace_id=result.run_id,
                        sequence=sequence,
                        step_type=StepType.EVALUATION,
                        content="\n".join(outcome.test_results),
                        metadata={
                            "verification": outcome.metadata.get("verification"),
                        },
                    )
                )
    reader = TraceReader(path)
    reader.read_header()
    tuple(reader.events())
    tuple(reader.steps())
    observation = RunObservation(
        output_text=outcome.output_text if outcome else "",
        commands=outcome.commands if outcome else (),
        changed_files=tuple(change.path for change in result.file_changes),
        task_text=task.instruction,
    )
    return RecordedReplayTrace(
        path=path,
        trace_id=result.run_id,
        request_id=request_id,
        observation=observation,
    )
