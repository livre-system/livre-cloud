-- Bind the active driver session to the native installation that logged in.
ALTER TABLE livre_driver_accounts
    ADD COLUMN IF NOT EXISTS active_device_id TEXT,
    ADD COLUMN IF NOT EXISTS device_session_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_livre_driver_accounts_active_device
    ON livre_driver_accounts (user_id, active_device_id)
    WHERE active = TRUE AND active_device_id IS NOT NULL;
