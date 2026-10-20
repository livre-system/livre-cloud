-- Estados operativos del flujo Livre.
ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS conflict_reason TEXT;

-- Compatibilidad con registros de las primeras versiones.
UPDATE livi_trips SET status = 'completed' WHERE status = 'done';
UPDATE livi_trips SET status = 'assigned' WHERE status IN ('confirmed', 'accepted');

ALTER TABLE livi_trips
    DROP CONSTRAINT IF EXISTS livi_trips_status_check;

ALTER TABLE livi_trips
    ADD CONSTRAINT livi_trips_status_check CHECK (
        status IN (
            'pending', 'searching', 'scheduled', 'assigned', 'arriving',
            'in_progress', 'conflict', 'lost', 'completed', 'cancelled'
        )
    ) NOT VALID;

CREATE INDEX IF NOT EXISTS idx_livi_trips_open_state
    ON livi_trips (status, created_at);
