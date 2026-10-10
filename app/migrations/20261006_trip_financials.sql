-- Persist one financial split for every Livre trip.
ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS gross_fare NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS livre_commission NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS driver_net NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS card_payment_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS financial_finalized_at TIMESTAMPTZ;

-- Existing quotes remain the passenger-facing gross fare.
UPDATE livi_trips
SET gross_fare = COALESCE(gross_fare, fare),
    livre_commission = ROUND(COALESCE(gross_fare, fare) * 0.15, 2),
    driver_net = COALESCE(gross_fare, fare) - ROUND(COALESCE(gross_fare, fare) * 0.15, 2),
    card_payment_confirmed = FALSE
WHERE status = 'completed' AND COALESCE(gross_fare, fare) IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_livi_trips_financial_finalized
    ON livi_trips (financial_finalized_at)
    WHERE financial_finalized_at IS NOT NULL;
