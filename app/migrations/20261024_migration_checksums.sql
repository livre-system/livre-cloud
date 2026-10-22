-- Metadata de integridad para migraciones aplicadas.
ALTER TABLE schema_migrations
    ADD COLUMN IF NOT EXISTS checksum TEXT;

CREATE INDEX IF NOT EXISTS idx_schema_migrations_applied_at
    ON schema_migrations (applied_at);
