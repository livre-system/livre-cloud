-- Migración: agregar whatsapp_phone como campo independiente de whatsapp_user
-- Tablas afectadas: livi_trips, livi_whatsapp_quotes
-- Seguro para ejecutar con la aplicación corriendo (ADD COLUMN es no bloqueante en Postgres).
-- Ejecutar ANTES de deployar el nuevo elevenlabs_trip.py.

ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS whatsapp_phone TEXT NOT NULL DEFAULT '';

ALTER TABLE livi_whatsapp_quotes
    ADD COLUMN IF NOT EXISTS whatsapp_phone TEXT NOT NULL DEFAULT '';

-- Índices (CONCURRENTLY = no bloquea lectura/escritura durante la creación)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_livi_trips_whatsapp_phone
    ON livi_trips (whatsapp_phone)
    WHERE whatsapp_phone <> '';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_livi_whatsapp_quotes_whatsapp_phone
    ON livi_whatsapp_quotes (whatsapp_phone)
    WHERE whatsapp_phone <> '';

-- Verificación post-ejecución (correr manualmente para confirmar):
-- SELECT table_name, column_name, data_type, column_default
-- FROM information_schema.columns
-- WHERE table_name IN ('livi_trips', 'livi_whatsapp_quotes')
--   AND column_name = 'whatsapp_phone'
-- ORDER BY table_name;
