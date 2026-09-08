-- migrations/003_add_user_mapping_and_livi_trips_fk.sql
-- Vinculación de usuarios del panel con identificadores de WhatsApp de LIVI

-- 1. Tabla de mapeo (idempotente)
CREATE TABLE IF NOT EXISTS user_whatsapp_mappings (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES livre_users(id) ON DELETE CASCADE,
    whatsapp_user TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(whatsapp_user)
);

-- 2. Columna en livi_trips (nullable para históricos)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'livi_trips' AND column_name = 'user_id'
    ) THEN
        ALTER TABLE livi_trips ADD COLUMN user_id TEXT;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_livi_trips_user_id ON livi_trips(user_id);
