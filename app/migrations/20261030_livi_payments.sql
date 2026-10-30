CREATE TABLE IF NOT EXISTS livi_payments (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    whatsapp_user TEXT NOT NULL,
    service_type_id TEXT NOT NULL,
    amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL DEFAULT 'ARS',
    status TEXT NOT NULL DEFAULT 'created',
    external_reference TEXT NOT NULL UNIQUE,
    provider_preference_id TEXT,
    provider_payment_id TEXT,
    init_point TEXT,
    trip_id BIGINT REFERENCES livi_trips(id) ON DELETE SET NULL,
    tracking_token TEXT,
    provider_response JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_livi_payments_active_quote
    ON livi_payments (quote_id)
    WHERE status NOT IN ('failed', 'rejected', 'cancelled');
CREATE INDEX IF NOT EXISTS idx_livi_payments_external_reference
    ON livi_payments (external_reference);
CREATE INDEX IF NOT EXISTS idx_livi_payments_provider_payment
    ON livi_payments (provider_payment_id);
