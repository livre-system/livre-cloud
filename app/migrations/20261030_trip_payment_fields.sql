ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS payment_status TEXT,
    ADD COLUMN IF NOT EXISTS payment_method TEXT,
    ADD COLUMN IF NOT EXISTS payment_provider TEXT,
    ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS payment_reference TEXT;

ALTER TABLE livi_payments
    ADD COLUMN IF NOT EXISTS payment_method TEXT,
    ADD COLUMN IF NOT EXISTS payment_type TEXT,
    ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_livi_trips_payment_status ON livi_trips (payment_status);
CREATE INDEX IF NOT EXISTS idx_livi_payments_trip_id ON livi_payments (trip_id);
