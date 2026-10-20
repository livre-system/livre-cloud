-- Backfill the closure table shape for databases created before financial snapshots.
-- Idempotent so deploy startup can safely apply it more than once.
ALTER TABLE livre_driver_shift_closures
    ADD COLUMN IF NOT EXISTS total_revenue NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS driver_revenue NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS commission NUMERIC(12, 2);
