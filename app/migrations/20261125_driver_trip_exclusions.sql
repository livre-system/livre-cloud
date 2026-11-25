-- Evita volver a ofrecer un viaje reasignado al conductor que lo liberó.
CREATE TABLE IF NOT EXISTS livre_trip_driver_exclusions (
    trip_id BIGINT NOT NULL REFERENCES livi_trips(id) ON DELETE CASCADE,
    driver_user_id TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trip_id, driver_user_id)
);
CREATE INDEX IF NOT EXISTS idx_livre_trip_driver_exclusions_driver
    ON livre_trip_driver_exclusions (driver_user_id, trip_id);
