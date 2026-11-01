ALTER TABLE livre_trip_messages
    ADD COLUMN IF NOT EXISTS client_message_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_livre_trip_messages_handie_client_id
    ON livre_trip_messages (handie_session_id, client_message_id)
    WHERE handie_session_id IS NOT NULL AND client_message_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_livre_trip_messages_client_id
    ON livre_trip_messages (trip_id, channel, client_message_id)
    WHERE trip_id IS NOT NULL AND client_message_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'livre_trip_messages_client_id_length'
    ) THEN
        ALTER TABLE livre_trip_messages
            ADD CONSTRAINT livre_trip_messages_client_id_length
            CHECK (client_message_id IS NULL OR char_length(client_message_id) BETWEEN 8 AND 100);
    END IF;
END $$;
