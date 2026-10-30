-- Muestras de continuidad del heartbeat del conductor.
-- No guarda coordenadas ni tokens: sólo permite detectar huecos de sesión.
CREATE TABLE IF NOT EXISTS livre_driver_heartbeat_diagnostics (
    driver_user_id TEXT NOT NULL REFERENCES livre_users(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL,
    bucket_start TIMESTAMPTZ NOT NULL,
    last_received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (driver_user_id, device_id, bucket_start)
);

CREATE INDEX IF NOT EXISTS idx_livre_driver_heartbeat_diagnostics_recent
    ON livre_driver_heartbeat_diagnostics (driver_user_id, last_received_at DESC);
