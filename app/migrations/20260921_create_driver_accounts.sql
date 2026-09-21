CREATE TABLE IF NOT EXISTS livre_driver_accounts (
    user_id TEXT PRIMARY KEY REFERENCES livre_users(id) ON DELETE CASCADE,
    magiis_driver_id BIGINT NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_livre_driver_accounts_magiis_driver
    ON livre_driver_accounts (magiis_driver_id)
    WHERE active = TRUE;
