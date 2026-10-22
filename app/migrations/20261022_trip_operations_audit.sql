-- Auditoría e idempotencia operativa de viajes.
-- Aplicar después de 20261020_trip_state_flow.sql.

CREATE TABLE IF NOT EXISTS livre_trip_audit_events (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT NOT NULL REFERENCES livi_trips(id) ON DELETE CASCADE,
    actor_user_id TEXT,
    actor_role TEXT NOT NULL,
    event_type TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT,
    request_id TEXT,
    event_key TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_livre_trip_audit_event_key
    ON livre_trip_audit_events (event_key);
CREATE INDEX IF NOT EXISTS idx_livre_trip_audit_trip_created
    ON livre_trip_audit_events (trip_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_livre_trip_audit_type_created
    ON livre_trip_audit_events (event_type, created_at DESC);

CREATE TABLE IF NOT EXISTS livre_trip_event_outbox (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT NOT NULL REFERENCES livi_trips(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_key TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_livre_trip_outbox_event_key
    ON livre_trip_event_outbox (event_key);
CREATE INDEX IF NOT EXISTS idx_livre_trip_outbox_pending
    ON livre_trip_event_outbox (available_at, id)
    WHERE status IN ('pending', 'failed');

CREATE INDEX IF NOT EXISTS idx_livi_trips_driver_status_created
    ON livi_trips (assigned_driver_user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_livi_trips_status_updated
    ON livi_trips (status, updated_at DESC);
