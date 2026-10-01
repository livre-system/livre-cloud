-- Persist the passenger-selected quote on the Livre trip itself.
ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS service_type_id TEXT,
    ADD COLUMN IF NOT EXISTS service_type_name TEXT,
    ADD COLUMN IF NOT EXISTS fare NUMERIC(12, 2);

CREATE INDEX IF NOT EXISTS idx_livi_trips_service_type
    ON livi_trips (service_type_id);
