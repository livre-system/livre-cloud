ALTER TABLE livre_driver_accounts
    ADD COLUMN IF NOT EXISTS active_device_id TEXT,
    ADD COLUMN IF NOT EXISTS device_session_active BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS device_last_seen_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS uq_livre_driver_accounts_active_device
    ON livre_driver_accounts (active_device_id)
    WHERE device_session_active = TRUE
      AND active_device_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_livre_driver_accounts_device_last_seen
    ON livre_driver_accounts (device_last_seen_at)
    WHERE device_session_active = TRUE;
