-- Snapshot immutable de cotizaciones confirmables de WhatsApp.
CREATE TABLE IF NOT EXISTS livi_whatsapp_quotes (
    quote_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    whatsapp_user TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'whatsapp' CHECK (source = 'whatsapp'),
    origen TEXT NOT NULL,
    destino TEXT NOT NULL,
    options JSONB NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '15 minutes'),
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_livi_whatsapp_quotes_conversation
    ON livi_whatsapp_quotes (conversation_id, created_at DESC);

ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS quote_id TEXT,
    ADD COLUMN IF NOT EXISTS original_fare NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS discount_percentage NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS discount_amount NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS final_fare NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS fare_source TEXT;

CREATE INDEX IF NOT EXISTS idx_livi_trips_quote_id ON livi_trips (quote_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_livi_trips_quote_id
    ON livi_trips (quote_id)
    WHERE quote_id IS NOT NULL;
