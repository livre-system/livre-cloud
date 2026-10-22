-- Roles CRM explícitos y mantenimiento seguro del outbox.
-- No modifica roles existentes: livreuser mantiene su compatibilidad temporal.
CREATE TABLE IF NOT EXISTS livre_user_roles (
    user_id TEXT NOT NULL REFERENCES livre_users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('crm', 'crm_admin', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role)
);

CREATE INDEX IF NOT EXISTS idx_livre_user_roles_role
    ON livre_user_roles (role, user_id);

CREATE INDEX IF NOT EXISTS idx_livre_trip_outbox_retention
    ON livre_trip_event_outbox (status, sent_at)
    WHERE status = 'sent';

ALTER TABLE livre_trip_event_outbox
    ADD COLUMN IF NOT EXISTS processing_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_livre_trip_outbox_processing
    ON livre_trip_event_outbox (processing_at)
    WHERE status = 'processing';
