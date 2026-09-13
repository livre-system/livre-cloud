CREATE TABLE IF NOT EXISTS magiis_crm_vehicle_owners (
    magiis_id BIGINT PRIMARY KEY,
    raw_payload JSONB NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);