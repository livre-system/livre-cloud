-- Keep shift closures aligned with persisted trip financials.
ALTER TABLE livre_driver_shift_closures
    ADD COLUMN IF NOT EXISTS total_commission NUMERIC(12, 2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_driver_net NUMERIC(12, 2) NOT NULL DEFAULT 0;
