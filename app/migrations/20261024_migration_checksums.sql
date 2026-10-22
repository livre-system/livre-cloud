-- Metadata de integridad para migraciones aplicadas.
ALTER TABLE schema_migrations
    ADD COLUMN IF NOT EXISTS checksum TEXT;

CREATE INDEX IF NOT EXISTS idx_schema_migrations_applied_at
    ON schema_migrations (applied_at);

CREATE TABLE IF NOT EXISTS livre_user_role_audit (
    id BIGSERIAL PRIMARY KEY,
    actor_user_id TEXT NOT NULL,
    target_user_id TEXT NOT NULL,
    previous_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    new_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_livre_user_role_audit_target_time
    ON livre_user_role_audit (target_user_id, changed_at DESC);
