CREATE TABLE IF NOT EXISTS magiis_crm_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    resources JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT
);

CREATE TABLE IF NOT EXISTS magiis_crm_passengers (
    magiis_id BIGINT PRIMARY KEY,
    raw_payload JSONB NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS magiis_crm_drivers (
    magiis_id BIGINT PRIMARY KEY,
    raw_payload JSONB NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS magiis_crm_vehicles (
    magiis_id BIGINT PRIMARY KEY,
    raw_payload JSONB NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS magiis_crm_travels (
    magiis_id BIGINT PRIMARY KEY,
    raw_payload JSONB NOT NULL,
    travel_date TIMESTAMPTZ,
    state TEXT,
    passenger_magiis_id BIGINT,
    driver_magiis_id BIGINT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_magiis_crm_travels_date
    ON magiis_crm_travels (travel_date);
CREATE INDEX IF NOT EXISTS idx_magiis_crm_travels_state
    ON magiis_crm_travels (state);
CREATE INDEX IF NOT EXISTS idx_magiis_crm_travels_passenger
    ON magiis_crm_travels (passenger_magiis_id);
CREATE INDEX IF NOT EXISTS idx_magiis_crm_travels_driver
    ON magiis_crm_travels (driver_magiis_id);
