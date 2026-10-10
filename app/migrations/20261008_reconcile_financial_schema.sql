-- Reconcile production databases that used the legacy financial column names.
-- Additive and idempotent: preserve existing trip and closure records.
ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS gross_fare NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS livre_commission NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS driver_net NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS card_payment_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS financial_status TEXT NOT NULL DEFAULT 'pending_settlement',
    ADD COLUMN IF NOT EXISTS financial_error TEXT,
    ADD COLUMN IF NOT EXISTS financial_finalized_at TIMESTAMPTZ;

-- Rebuild the canonical Livre split from the persisted gross fare.
-- Legacy values remain untouched; only the canonical columns are populated.
UPDATE livi_trips
SET gross_fare = COALESCE(gross_fare, fare),
    livre_commission = CASE
        WHEN COALESCE(gross_fare, fare) IS NULL THEN NULL
        ELSE ROUND(COALESCE(gross_fare, fare) * 0.15, 2)
    END,
    driver_net = CASE
        WHEN COALESCE(gross_fare, fare) IS NULL THEN NULL
        ELSE COALESCE(gross_fare, fare) - ROUND(COALESCE(gross_fare, fare) * 0.15, 2)
    END,
    card_payment_confirmed = CASE
        WHEN payment_confirmed IS TRUE THEN TRUE
        ELSE COALESCE(card_payment_confirmed, FALSE)
    END,
    financial_status = CASE
        WHEN status = 'completed' AND COALESCE(gross_fare, fare) IS NOT NULL THEN 'finalized'
        ELSE 'pending_settlement'
    END,
    financial_error = CASE
        WHEN status = 'completed' AND COALESCE(gross_fare, fare) IS NULL
            THEN 'gross fare missing at trip finalization'
        ELSE NULL
    END,
    financial_finalized_at = CASE
        WHEN status = 'completed' AND COALESCE(gross_fare, fare) IS NOT NULL
            THEN COALESCE(financial_finalized_at, completed_at, NOW())
        ELSE NULL
    END
WHERE gross_fare IS NULL
   OR livre_commission IS NULL
   OR driver_net IS NULL
   OR financial_status = 'pending_settlement';

ALTER TABLE livre_driver_shift_closures
    ADD COLUMN IF NOT EXISTS total_commission NUMERIC(12, 2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_driver_net NUMERIC(12, 2) NOT NULL DEFAULT 0;

UPDATE livre_driver_shift_closures c
SET total_commission = CASE
        WHEN c.total_commission = 0 THEN COALESCE(
            (SELECT SUM(t.livre_commission)
             FROM livre_driver_shift_closure_trips ct
             JOIN livi_trips t ON t.id = ct.trip_id
             WHERE ct.closure_id = c.id),
            0
        )
        ELSE c.total_commission
    END,
    total_driver_net = CASE
        WHEN c.total_driver_net = 0 THEN COALESCE(
            (SELECT SUM(t.driver_net)
             FROM livre_driver_shift_closure_trips ct
             JOIN livi_trips t ON t.id = ct.trip_id
             WHERE ct.closure_id = c.id),
            0
        )
        ELSE c.total_driver_net
    END
WHERE c.total_commission = 0 OR c.total_driver_net = 0;

CREATE INDEX IF NOT EXISTS idx_livi_trips_financial_finalized
    ON livi_trips (financial_finalized_at)
    WHERE financial_finalized_at IS NOT NULL;
