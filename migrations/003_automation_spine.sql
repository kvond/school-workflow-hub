-- Shared, local-only orchestration ledger.  These records describe work and
-- approvals; they do not authorize or perform an external operation.

CREATE TABLE IF NOT EXISTS account_bindings (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    account_alias TEXT NOT NULL,
    binding_reference TEXT NOT NULL,
    verification_status TEXT NOT NULL CHECK (verification_status IN ('unverified', 'verified', 'revoked')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    UNIQUE(provider, account_alias)
);

CREATE TABLE IF NOT EXISTS work_items (
    id TEXT PRIMARY KEY,
    created_run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    target_system TEXT NOT NULL,
    target_stable_id TEXT NOT NULL,
    required_permission TEXT NOT NULL,
    plan_hash TEXT NOT NULL UNIQUE,
    plan_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'blocked', 'completed', 'superseded')),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_work_items_target
    ON work_items(target_system, target_stable_id, status);

CREATE TABLE IF NOT EXISTS work_item_dependencies (
    work_item_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE RESTRICT,
    depends_on_work_item_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE RESTRICT,
    dependency_kind TEXT NOT NULL DEFAULT 'requires',
    PRIMARY KEY (work_item_id, depends_on_work_item_id)
);

CREATE TABLE IF NOT EXISTS run_work_items (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    work_item_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE RESTRICT,
    role TEXT NOT NULL DEFAULT 'planned',
    PRIMARY KEY (run_id, work_item_id, role)
);

CREATE TABLE IF NOT EXISTS run_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    work_item_id TEXT REFERENCES work_items(id) ON DELETE RESTRICT,
    step_index INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'blocked', 'denied')),
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(run_id, step_index)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    work_item_id TEXT REFERENCES work_items(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    reference TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(run_id, kind, reference, content_hash)
);

CREATE TABLE IF NOT EXISTS approval_grants (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES work_items(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    target_system TEXT NOT NULL,
    target_stable_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    grant_nonce TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'consumed', 'expired')),
    expires_at TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    consumed_at TEXT,
    revoked_at TEXT,
    UNIQUE(actor_id, work_item_id, grant_nonce)
);

CREATE INDEX IF NOT EXISTS idx_approval_grants_active
    ON approval_grants(work_item_id, status, expires_at);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES runs(id) ON DELETE RESTRICT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_entity
    ON audit_events(entity_type, entity_id, created_at);

-- The plan itself is evidence.  Its identity cannot be edited after it has
-- been reviewed; a change must be represented by a new work item and hash.
CREATE TRIGGER IF NOT EXISTS work_items_plan_is_immutable
BEFORE UPDATE OF kind, target_system, target_stable_id, required_permission, plan_hash, plan_json
ON work_items
BEGIN
    SELECT RAISE(ABORT, 'work item plan identity is immutable');
END;
