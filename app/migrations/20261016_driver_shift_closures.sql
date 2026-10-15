-- Persistent, per-driver daily snapshots. A unique date prevents duplicate closes.
CREATE TABLE IF NOT EXISTS livre_driver_shift_closures (
    id BIGSERIAL PRIMARY KEY,
    driver_user_id TEXT NOT NULL REFERENCES livre_users(id) ON DELETE CASCADE,
    closure_date DATE NOT NULL,
    trip_count INTEGER NOT NULL CHECK (trip_count >= 0),
    total_revenue NUMERIC(12, 2),
    driver_revenue NUMERIC(12, 2),
    commission NUMERIC(12, 2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (driver_user_id, closure_date)
);

CREATE INDEX IF NOT EXISTS idx_livre_driver_shift_closures_driver_date
    ON livre_driver_shift_closures (driver_user_id, closure_date DESC);
