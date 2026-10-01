-- Auditoría del mensaje de seguimiento enviado por ElevenLabs.
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_message_sent_at TIMESTAMPTZ;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_message_conversation_id TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS tracking_message_error TEXT;
