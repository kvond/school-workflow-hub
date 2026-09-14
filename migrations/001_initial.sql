CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    workflow TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    external_writes INTEGER NOT NULL DEFAULT 0 CHECK (external_writes >= 0),
    live_model_calls INTEGER NOT NULL DEFAULT 0 CHECK (live_model_calls >= 0),
    detail_json TEXT NOT NULL DEFAULT '{}',
    error_type TEXT,
    error_message TEXT,
    UNIQUE(workflow, input_hash)
);

CREATE TABLE IF NOT EXISTS source_revisions (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES runs(id) ON DELETE RESTRICT,
    source_type TEXT NOT NULL,
    stable_object_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    UNIQUE(source_type, stable_object_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_source_revisions_active
    ON source_revisions(source_type, stable_object_id, active);

CREATE INDEX IF NOT EXISTS idx_source_revisions_run
    ON source_revisions(run_id);

CREATE TABLE IF NOT EXISTS proposed_actions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    action_type TEXT NOT NULL,
    target_system TEXT NOT NULL,
    target_stable_id TEXT NOT NULL,
    source_stable_id TEXT NOT NULL,
    source_revision_id TEXT NOT NULL REFERENCES source_revisions(id) ON DELETE RESTRICT,
    expected_state_json TEXT NOT NULL,
    proposed_state_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    required_permission TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending',
    state TEXT NOT NULL DEFAULT 'proposed',
    idempotency_key TEXT NOT NULL UNIQUE,
    expires_at TEXT,
    verification_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proposed_actions_source
    ON proposed_actions(source_stable_id, state);

CREATE TABLE IF NOT EXISTS execution_attempts (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL REFERENCES proposed_actions(id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    reconcile_required INTEGER NOT NULL DEFAULT 0 CHECK (reconcile_required IN (0, 1)),
    UNIQUE(action_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS review_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    action_id TEXT REFERENCES proposed_actions(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
