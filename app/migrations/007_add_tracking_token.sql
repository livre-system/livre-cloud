-- Token privado para enlazar un viaje de WhatsApp con la vista de pasajeros.
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_token TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_livi_trips_tracking_token
    ON livi_trips (tracking_token)
    WHERE tracking_token IS NOT NULL;