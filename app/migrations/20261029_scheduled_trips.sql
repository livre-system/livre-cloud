ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_livi_trips_scheduled_at
    ON livi_trips (status, scheduled_at)
    WHERE status = 'scheduled';