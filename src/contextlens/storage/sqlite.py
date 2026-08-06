"""Project-scoped SQLite store for queryable ContextLens state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from contextlens.trace.model import AgentTrace, ContextEvent, TraceStep

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retention_days INTEGER
);

CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_type TEXT NOT NULL,
    model_provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT,
    task TEXT NOT NULL,
    repository_url TEXT,
    repository_commit TEXT,
    environment_image TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    total_input_tokens INTEGER NOT NULL,
    total_output_tokens INTEGER NOT NULL,
    total_cached_tokens INTEGER NOT NULL,
    total_tool_calls INTEGER NOT NULL,
    total_runtime_ms INTEGER NOT NULL,
    baseline_score REAL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trace_steps (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    step_type TEXT NOT NULL,
    input_context_item_ids_json TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cached_tokens INTEGER,
    duration_ms INTEGER,
    tool_name TEXT,
    tool_input_json TEXT,
    tool_output_reference TEXT,
    content TEXT,
    metadata_json TEXT NOT NULL,
    UNIQUE(trace_id, sequence)
);

CREATE TABLE IF NOT EXISTS context_items (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_uri TEXT,
    content TEXT,
    content_reference_json TEXT,
    content_hash TEXT NOT NULL,
    token_count INTEGER,
    token_count_method TEXT,
    inserted_at_step INTEGER NOT NULL,
    insertion_position INTEGER NOT NULL,
    target_agent_id TEXT,
    target_phase TEXT,
    metadata_json TEXT NOT NULL,
    tags_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_item_id TEXT NOT NULL REFERENCES context_items(id) ON DELETE CASCADE,
    relevance_score REAL NOT NULL,
    observed_usage_score REAL NOT NULL,
    redundancy_score REAL NOT NULL,
    contradiction_score REAL NOT NULL,
    staleness_score REAL NOT NULL,
    token_cost_score REAL NOT NULL,
    experiment_priority REAL NOT NULL,
    reasons_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    budget INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_variants (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    is_baseline INTEGER NOT NULL,
    configuration_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mutations (
    id TEXT PRIMARY KEY,
    variant_id TEXT NOT NULL REFERENCES experiment_variants(id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    context_item_id TEXT NOT NULL REFERENCES context_items(id) ON DELETE CASCADE,
    target_tokens INTEGER,
    target_agent_ids_json TEXT,
    target_phases_json TEXT,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replay_runs (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL REFERENCES experiment_variants(id) ON DELETE CASCADE,
    pair_id TEXT NOT NULL,
    job_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    error TEXT,
    output_directory TEXT,
    tokens_input INTEGER,
    tokens_output INTEGER,
    runtime_ms INTEGER,
    tool_calls INTEGER,
    retries INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_results (
    id TEXT PRIMARY KEY,
    replay_run_id TEXT NOT NULL REFERENCES replay_runs(id) ON DELETE CASCADE,
    success INTEGER NOT NULL,
    utility_score REAL NOT NULL,
    task_completion REAL,
    tests REAL,
    build REAL,
    type_check REAL,
    lint REAL,
    patch_quality REAL,
    patch_scope REAL,
    evidence_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS effect_estimates (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    mutation_id TEXT NOT NULL REFERENCES mutations(id) ON DELETE CASCADE,
    baseline_mean REAL NOT NULL,
    variant_mean REAL NOT NULL,
    absolute_difference REAL NOT NULL,
    relative_difference REAL,
    token_difference REAL,
    runtime_difference REAL,
    tool_call_difference REAL,
    paired_runs INTEGER NOT NULL,
    variance REAL NOT NULL,
    confidence_low REAL,
    confidence_high REAL,
    evidence_quality TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    context_item_id TEXT NOT NULL REFERENCES context_items(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    confidence TEXT NOT NULL,
    estimated_savings_tokens REAL,
    risks_json TEXT NOT NULL,
    experiment_count INTEGER NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS context_policies (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    objective TEXT NOT NULL,
    version INTEGER NOT NULL,
    policy_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_traces_project ON traces(project_id);
CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at);
CREATE INDEX IF NOT EXISTS idx_steps_trace ON trace_steps(trace_id);
CREATE INDEX IF NOT EXISTS idx_context_trace ON context_items(trace_id);
CREATE INDEX IF NOT EXISTS idx_context_hash ON context_items(content_hash);
CREATE INDEX IF NOT EXISTS idx_profiles_context ON context_profiles(context_item_id);
CREATE INDEX IF NOT EXISTS idx_experiments_trace ON experiments(trace_id);
CREATE INDEX IF NOT EXISTS idx_experiments_project ON experiments(project_id);
CREATE INDEX IF NOT EXISTS idx_replays_experiment ON replay_runs(experiment_id);
CREATE INDEX IF NOT EXISTS idx_replays_status ON replay_runs(status);
CREATE INDEX IF NOT EXISTS idx_recommendations_trace ON recommendations(trace_id);
"""


class ContextLensStore:
    """Small normalized store with project ownership on every content read."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(SCHEMA_SQL)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create_project(
        self,
        project_id: str,
        name: str,
        *,
        retention_days: int | None = None,
    ) -> None:
        if not project_id or not name:
            raise ValueError("project ID and name cannot be empty")
        if retention_days is not None and retention_days < 1:
            raise ValueError("retention_days must be positive")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO projects(id, name, retention_days) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    retention_days = excluded.retention_days
                """,
                (project_id, name, retention_days),
            )

    def save_trace(
        self,
        trace: AgentTrace,
        *,
        steps: Sequence[TraceStep] = (),
        context_events: Sequence[ContextEvent] = (),
    ) -> None:
        with self._connection() as connection:
            self._require_project(connection, trace.project_id)
            connection.execute(
                """
                INSERT INTO traces VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(id) DO UPDATE SET
                    completed_at=excluded.completed_at,
                    status=excluded.status,
                    total_input_tokens=excluded.total_input_tokens,
                    total_output_tokens=excluded.total_output_tokens,
                    total_cached_tokens=excluded.total_cached_tokens,
                    total_tool_calls=excluded.total_tool_calls,
                    total_runtime_ms=excluded.total_runtime_ms,
                    baseline_score=excluded.baseline_score,
                    metadata_json=excluded.metadata_json
                """,
                (
                    trace.trace_id,
                    trace.project_id,
                    trace.agent_type,
                    trace.model_provider,
                    trace.model_name,
                    trace.model_version,
                    trace.task,
                    trace.repository_url,
                    trace.repository_commit,
                    trace.environment_image,
                    trace.started_at,
                    trace.completed_at,
                    trace.status.value,
                    trace.total_input_tokens,
                    trace.total_output_tokens,
                    trace.total_cached_tokens,
                    trace.total_tool_calls,
                    trace.total_runtime_ms,
                    trace.baseline_score,
                    _json(trace.metadata),
                ),
            )
            for step in steps:
                self._save_step(connection, trace.trace_id, step)
            for event in context_events:
                self._save_context(connection, trace.trace_id, event)

    def get_trace(self, project_id: str, trace_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM traces WHERE id = ? AND project_id = ?",
                (trace_id, project_id),
            ).fetchone()
            if row is None:
                raise KeyError(
                    f"trace {trace_id!r} not found in project {project_id!r}"
                )
            result = dict(row)
            result["metadata"] = json.loads(result.pop("metadata_json"))
            return result

    def context_items(
        self,
        project_id: str,
        trace_id: str,
    ) -> tuple[dict[str, Any], ...]:
        with self._connection() as connection:
            self._require_trace(connection, project_id, trace_id)
            rows = connection.execute(
                """
                SELECT c.* FROM context_items c
                WHERE c.trace_id = ?
                ORDER BY c.inserted_at_step, c.insertion_position, c.id
                """,
                (trace_id,),
            ).fetchall()
            return tuple(dict(row) for row in rows)

    def claim_replay_job(
        self,
        *,
        job_id: str,
        run_id: str,
        experiment_id: str,
        variant_id: str,
        pair_id: str,
    ) -> bool:
        """Atomically claim a stable replay job; false means already claimed."""

        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO replay_runs(
                    id, experiment_id, variant_id, pair_id, job_id,
                    status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, 'pending', '{}')
                """,
                (run_id, experiment_id, variant_id, pair_id, job_id),
            )
            return cursor.rowcount == 1

    def update_replay_status(
        self,
        job_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        allowed = {
            "pending",
            "running",
            "evaluating",
            "completed",
            "failed",
            "cancelled",
            "timed_out",
        }
        if status not in allowed:
            raise ValueError(f"invalid replay status: {status}")
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE replay_runs SET status = ?, error = ? WHERE job_id = ?",
                (status, error, job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown replay job: {job_id}")

    def delete_trace(self, project_id: str, trace_id: str) -> None:
        with self._connection() as connection:
            self._require_trace(connection, project_id, trace_id)
            connection.execute(
                "DELETE FROM traces WHERE id = ? AND project_id = ?",
                (trace_id, project_id),
            )

    @staticmethod
    def _require_project(
        connection: sqlite3.Connection,
        project_id: str,
    ) -> None:
        row = connection.execute(
            "SELECT 1 FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown project: {project_id}")

    @staticmethod
    def _require_trace(
        connection: sqlite3.Connection,
        project_id: str,
        trace_id: str,
    ) -> None:
        row = connection.execute(
            "SELECT 1 FROM traces WHERE id = ? AND project_id = ?",
            (trace_id, project_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"trace {trace_id!r} not found in project {project_id!r}")

    @staticmethod
    def _save_step(
        connection: sqlite3.Connection,
        trace_id: str,
        step: TraceStep,
    ) -> None:
        if step.trace_id != trace_id:
            raise ValueError("step belongs to a different trace")
        usage = step.token_usage
        connection.execute(
            """
            INSERT OR REPLACE INTO trace_steps VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                step.step_id,
                trace_id,
                step.sequence,
                step.step_type.value,
                _json(step.input_context_item_ids),
                usage.input if usage else None,
                usage.output if usage else None,
                usage.cached if usage else None,
                step.duration_ms,
                step.tool_name,
                _json(step.tool_input) if step.tool_input is not None else None,
                step.tool_output_reference,
                step.content,
                _json(step.metadata),
            ),
        )

    @staticmethod
    def _save_context(
        connection: sqlite3.Connection,
        trace_id: str,
        event: ContextEvent,
    ) -> None:
        source = event.source
        connection.execute(
            """
            INSERT OR REPLACE INTO context_items VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                source.source_id,
                trace_id,
                event.request_id,
                source.kind.value,
                source.name,
                source.source_uri,
                source.content,
                (
                    _json(source.content_ref.to_dict())
                    if source.content_ref is not None
                    else None
                ),
                source.content_hash,
                source.token_count,
                source.token_count_method,
                source.inserted_at_step,
                source.insertion_position or event.sequence,
                source.target_agent_id,
                source.target_phase,
                _json(source.provenance),
                _json(source.tags),
            ),
        )


def _json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: Any) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")
