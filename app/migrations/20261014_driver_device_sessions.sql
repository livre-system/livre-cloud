-- Keep driver authentication independent per native installation.
CREATE TABLE IF NOT EXISTS livre_driver_device_sessions (
    user_id TEXT NOT NULL REFERENCES livre_users(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    PRIMARY KEY (user_id, device_id)
);

-- Preserve the session created by the previous single-device implementation.
INSERT INTO livre_driver_device_sessions (user_id, device_id, active, created_at, last_seen_at)
SELECT user_id, active_device_id, TRUE, COALESCE(device_session_updated_at, NOW()), NOW()
FROM livre_driver_accounts
WHERE active = TRUE AND active_device_id IS NOT NULL
ON CONFLICT (user_id, device_id) DO UPDATE
SET active = TRUE,
    revoked_at = NULL,
    last_seen_at = EXCLUDED.last_seen_at;

CREATE INDEX IF NOT EXISTS idx_livre_driver_device_sessions_active
    ON livre_driver_device_sessions (user_id, device_id)
    WHERE active = TRUE;
