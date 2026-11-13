-- Preserve the WhatsApp identity independently from legacy whatsapp_user.
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS whatsapp_user_id TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_message_status TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_message_wamid TEXT;
CREATE INDEX IF NOT EXISTS idx_livi_trips_whatsapp_user_id
    ON livi_trips (whatsapp_user_id)
    WHERE whatsapp_user_id IS NOT NULL AND whatsapp_user_id <> '';
