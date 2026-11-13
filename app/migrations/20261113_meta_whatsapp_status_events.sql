CREATE TABLE IF NOT EXISTS livre_whatsapp_message_events (
    wamid TEXT PRIMARY KEY,
    recipient_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    status_timestamp TIMESTAMPTZ,
    error_code TEXT,
    error_message TEXT,
    payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_livre_whatsapp_message_events_recipient
    ON livre_whatsapp_message_events (recipient_id, status_timestamp DESC);
