-- Datos públicos del viaje, proyectados por el CRM.
ALTER TABLE magiis_crm_travels ADD COLUMN IF NOT EXISTS tracking_driver_name TEXT;
ALTER TABLE magiis_crm_travels ADD COLUMN IF NOT EXISTS tracking_plate TEXT;
ALTER TABLE magiis_crm_travels ADD COLUMN IF NOT EXISTS tracking_vehicle_color TEXT;
ALTER TABLE magiis_crm_travels ADD COLUMN IF NOT EXISTS tracking_vehicle_model TEXT;

ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_vehicle_color TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_driver_name TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_plate TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_vehicle_color TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_vehicle_model TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_latitude DOUBLE PRECISION;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_longitude DOUBLE PRECISION;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_location_updated_at TIMESTAMPTZ;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_location_visible_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_livi_trips_tracking_visible_at
    ON livi_trips (tracking_location_visible_at);
