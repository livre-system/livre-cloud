-- Repara instalaciones donde la migración del handie quedó registrada
-- pero livre_trip_messages no recibió la columna de correlación.
ALTER TABLE livre_trip_messages
    ADD COLUMN IF NOT EXISTS handie_session_id UUID;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'livre_trip_messages_handie_fk'
    ) THEN
        ALTER TABLE livre_trip_messages
            ADD CONSTRAINT livre_trip_messages_handie_fk
            FOREIGN KEY (handie_session_id)
            REFERENCES livre_handie_sessions(id)
            ON DELETE CASCADE;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_livre_trip_messages_handie_session
    ON livre_trip_messages (handie_session_id, created_at);
