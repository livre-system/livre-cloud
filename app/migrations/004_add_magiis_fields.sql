-- Coordenadas y estado de sincronización con MAGIIS.
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS origen_latitude DOUBLE PRECISION;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS origen_longitude DOUBLE PRECISION;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS destino_latitude DOUBLE PRECISION;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS destino_longitude DOUBLE PRECISION;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_travel_id BIGINT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_status TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_response TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_error TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_updated_at TIMESTAMPTZ;
