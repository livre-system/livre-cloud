ALTER TABLE livre_driver_accounts
    ADD COLUMN IF NOT EXISTS operational_status TEXT NOT NULL DEFAULT 'available';

UPDATE livre_driver_accounts
SET operational_status = 'available'
WHERE operational_status IS NULL;

ALTER TABLE livre_driver_accounts
    DROP CONSTRAINT IF EXISTS livre_driver_accounts_operational_status_check;

ALTER TABLE livre_driver_accounts
    ADD CONSTRAINT livre_driver_accounts_operational_status_check
    CHECK (operational_status IN ('available', 'unavailable', 'out_of_service'));
