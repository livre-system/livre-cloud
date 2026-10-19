-- Make operational KPIs auditable without rewriting historical trips.
ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS activity_type TEXT NOT NULL DEFAULT 'unclassified',
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'unknown';

CREATE INDEX IF NOT EXISTS idx_livi_trips_operational_activity
    ON livi_trips (status, activity_type, source, created_at);

CREATE TABLE IF NOT EXISTS livi_operational_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    activity_type TEXT NOT NULL DEFAULT 'unclassified',
    status TEXT NOT NULL DEFAULT 'recorded',
    external_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_livi_operational_events_external
    ON livi_operational_events (source, external_id)
    WHERE external_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_livi_operational_events_reporting
    ON livi_operational_events (event_type, activity_type, source, occurred_at);
