-- Un vehículo puede ser utilizado por varios choferes.
-- La identidad operativa siempre es el usuario del chofer.
CREATE TABLE IF NOT EXISTS livre_driver_vehicles (
    driver_user_id TEXT NOT NULL REFERENCES livre_users(id) ON DELETE CASCADE,
    vehicle_magiis_id BIGINT NOT NULL,
    vehicle_label TEXT,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (driver_user_id, vehicle_magiis_id)
);

CREATE INDEX IF NOT EXISTS idx_livre_driver_vehicles_vehicle
    ON livre_driver_vehicles (vehicle_magiis_id);