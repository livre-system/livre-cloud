-- Make operational KPIs auditable without rewriting historical trips.
-- These columns stay nullable so existing rows remain untouched and unclassified.
-- New writers must provide activity_type/source explicitly when known.
ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS activity_type TEXT,
    ADD COLUMN IF NOT EXISTS source TEXT;

CREATE INDEX IF NOT EXISTS idx_livi_trips_operational_activity
    ON livi_trips (status, activity_type, source, created_at);
