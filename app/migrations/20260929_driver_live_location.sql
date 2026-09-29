-- Ubicación permanente del conductor, independiente de un viaje.
ALTER TABLE livre_driver_accounts
    ADD COLUMN IF NOT EXISTS current_latitude DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS current_longitude DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS location_updated_at TIMESTAMPTZ;
