
from app.core.database import get_conn
migration_sql = """
CREATE TABLE IF NOT EXISTS user_whatsapp_mappings (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES livre_users(id) ON DELETE CASCADE,
    whatsapp_user TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(whatsapp_user)
);

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
"""
try:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(migration_sql)
    print("Migración aplicada correctamente.")
except Exception as e:
    print(f"Error en migración: {e}")
