ALTER TABLE livi_whatsapp_quotes
    DROP CONSTRAINT IF EXISTS livi_whatsapp_quotes_source_check;

ALTER TABLE livi_whatsapp_quotes
    ADD CONSTRAINT livi_whatsapp_quotes_source_check
    CHECK (source IN ('whatsapp', 'passenger'));

ALTER TABLE livi_payments
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'whatsapp',
    ADD COLUMN IF NOT EXISTS user_id TEXT;

CREATE INDEX IF NOT EXISTS idx_livi_payments_user_id ON livi_payments (user_id);
