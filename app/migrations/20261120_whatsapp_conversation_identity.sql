-- Persist inbound WhatsApp identity from ElevenLabs conversation initialization.
CREATE TABLE IF NOT EXISTS livi_whatsapp_conversation_identity (
    conversation_id TEXT PRIMARY KEY,
    whatsapp_user TEXT NOT NULL DEFAULT '',
    whatsapp_phone TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_livi_whatsapp_identity_updated
    ON livi_whatsapp_conversation_identity (updated_at DESC);
