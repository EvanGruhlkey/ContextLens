PRAGMA foreign_keys = ON;

CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retention_days INTEGER
);

CREATE TABLE traces (
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

CREATE TABLE trace_steps (
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

CREATE TABLE context_items (
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

CREATE TABLE context_profiles (
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

CREATE TABLE experiments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    budget INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT NOT NULL
);

CREATE TABLE experiment_variants (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    is_baseline INTEGER NOT NULL,
    configuration_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE mutations (
    id TEXT PRIMARY KEY,
    variant_id TEXT NOT NULL REFERENCES experiment_variants(id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    context_item_id TEXT NOT NULL REFERENCES context_items(id) ON DELETE CASCADE,
    target_tokens INTEGER,
    target_agent_ids_json TEXT,
    target_phases_json TEXT,
    metadata_json TEXT NOT NULL
);

CREATE TABLE replay_runs (
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

CREATE TABLE evaluation_results (
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

CREATE TABLE effect_estimates (
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

CREATE TABLE recommendations (
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

CREATE TABLE context_policies (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    objective TEXT NOT NULL,
    version INTEGER NOT NULL,
    policy_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_traces_project ON traces(project_id);
CREATE INDEX idx_traces_started ON traces(started_at);
CREATE INDEX idx_steps_trace ON trace_steps(trace_id);
CREATE INDEX idx_context_trace ON context_items(trace_id);
CREATE INDEX idx_context_hash ON context_items(content_hash);
CREATE INDEX idx_profiles_context ON context_profiles(context_item_id);
CREATE INDEX idx_experiments_trace ON experiments(trace_id);
CREATE INDEX idx_experiments_project ON experiments(project_id);
CREATE INDEX idx_replays_experiment ON replay_runs(experiment_id);
CREATE INDEX idx_replays_status ON replay_runs(status);
CREATE INDEX idx_recommendations_trace ON recommendations(trace_id);
