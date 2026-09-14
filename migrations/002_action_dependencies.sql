CREATE TABLE IF NOT EXISTS action_source_dependencies (
    action_id TEXT NOT NULL REFERENCES proposed_actions(id) ON DELETE RESTRICT,
    source_revision_id TEXT NOT NULL REFERENCES source_revisions(id) ON DELETE RESTRICT,
    dependency_role TEXT NOT NULL,
    PRIMARY KEY (action_id, source_revision_id),
    UNIQUE (action_id, dependency_role)
);

CREATE INDEX IF NOT EXISTS idx_action_source_dependencies_revision
    ON action_source_dependencies(source_revision_id);

INSERT OR IGNORE INTO action_source_dependencies(action_id, source_revision_id, dependency_role)
SELECT id, source_revision_id, 'primary'
FROM proposed_actions;
