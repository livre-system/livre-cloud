-- migrations/002_create_livi_trips.sql
-- Livre Cloud — Tabla para pedidos de viaje registrados por LIVI
--
-- Ejecutar UNA SOLA VEZ:
--   psql $DATABASE_URL -f migrations/002_create_livi_trips.sql

CREATE TABLE IF NOT EXISTS livi_trips (
    id                  SERIAL PRIMARY KEY,
    conversation_id     TEXT        NOT NULL,           -- ID de conversación de ElevenLabs
    origen              TEXT        NOT NULL,           -- Dirección de origen confirmada
    destino             TEXT        NOT NULL,           -- Dirección de destino confirmada
    whatsapp_user       TEXT,                          -- Número WhatsApp del pasajero
    tool_call_id        TEXT,                          -- ID del tool call de ElevenLabs
    status              TEXT        NOT NULL DEFAULT 'pending',  -- pending / confirmed / cancelled
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índice único por conversation_id — idempotencia: una conversación = un viaje
CREATE UNIQUE INDEX IF NOT EXISTS idx_livi_trips_conversation_id
    ON livi_trips (conversation_id);

-- Índice para búsquedas por whatsapp
CREATE INDEX IF NOT EXISTS idx_livi_trips_whatsapp
    ON livi_trips (whatsapp_user);

-- Verificación
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'livi_trips'
ORDER BY ordinal_position;
