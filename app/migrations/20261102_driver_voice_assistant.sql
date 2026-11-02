-- Permite distinguir en CRM la voz de Livi de los mensajes del chofer.
ALTER TABLE livre_trip_messages
    DROP CONSTRAINT IF EXISTS livre_trip_messages_sender_role_check;

ALTER TABLE livre_trip_messages
    ADD CONSTRAINT livre_trip_messages_sender_role_check
    CHECK (sender_role IN ('passenger', 'driver', 'base', 'assistant'));
