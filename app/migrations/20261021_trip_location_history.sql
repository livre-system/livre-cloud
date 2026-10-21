-- Historial GPS mínimo de viajes Livre.
-- Solo se insertan puntos de viajes aceptados/en curso; los puntos se conservan
-- únicamente cuando el viaje termina correctamente.

CREATE TABLE IF NOT EXISTS livre_trip_location_history (
    id BIGSERIAL PRIMARY KEY,
    trip_id INTEGER NOT NULL REFERENCES livi_trips(id) ON DELETE CASCADE,
    driver_user_id TEXT NOT NULL REFERENCES livre_users(id) ON DELETE RESTRICT,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_livre_trip_location_history_trip_time
    ON livre_trip_location_history (trip_id, recorded_at);

CREATE INDEX IF NOT EXISTS idx_livre_trip_location_history_driver
    ON livre_trip_location_history (driver_user_id);
