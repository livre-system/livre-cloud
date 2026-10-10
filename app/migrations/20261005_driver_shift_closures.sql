-- Cierre diario operativo de Livre; no depende de MAGIIS.
ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_livi_trips_driver_completed
    ON livi_trips (assigned_driver_user_id, completed_at)
    WHERE assigned_driver_user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS livre_driver_shift_closures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_user_id TEXT NOT NULL REFERENCES livre_users(id) ON DELETE RESTRICT,
    closure_date DATE NOT NULL,
    closed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trip_count INTEGER NOT NULL CHECK (trip_count >= 0),
    total_revenue NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (total_revenue >= 0),
    vehicle_id TEXT,
    vehicle_label TEXT,
    vehicle_model TEXT,
    plate TEXT,
    owner_name TEXT,
    pdf_generated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_livre_driver_shift_closure_day
    ON livre_driver_shift_closures (driver_user_id, closure_date);

CREATE TABLE IF NOT EXISTS livre_driver_shift_closure_trips (
    closure_id UUID NOT NULL REFERENCES livre_driver_shift_closures(id) ON DELETE CASCADE,
    trip_id INTEGER NOT NULL REFERENCES livi_trips(id) ON DELETE RESTRICT,
    fare NUMERIC(12, 2),
    PRIMARY KEY (closure_id, trip_id),
    UNIQUE (trip_id)
);

CREATE INDEX IF NOT EXISTS idx_livre_shift_closure_trips_trip
    ON livre_driver_shift_closure_trips (trip_id);

ALTER TABLE livre_driver_vehicles
    ADD COLUMN IF NOT EXISTS vehicle_id TEXT,
    ADD COLUMN IF NOT EXISTS plate TEXT,
    ADD COLUMN IF NOT EXISTS vehicle_model TEXT,
    ADD COLUMN IF NOT EXISTS owner_name TEXT;
