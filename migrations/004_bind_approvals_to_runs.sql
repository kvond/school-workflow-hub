-- A consumed local dry-run approval can only be reconciled in the run that
-- originally received it. Existing historical rows remain NULL and therefore
-- fail closed under the updated authorization code.
ALTER TABLE approval_grants
    ADD COLUMN approved_run_id TEXT REFERENCES runs(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_approval_grants_run
    ON approval_grants(approved_run_id, status, expires_at);
