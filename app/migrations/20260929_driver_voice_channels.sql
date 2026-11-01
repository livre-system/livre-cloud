-- Canales propios de Livre para chat de viaje y comunicación con base.
-- No comunica directamente con MAGIIS ni expone credenciales externas.
CREATE TABLE IF NOT EXISTS livre_trip_messages (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT REFERENCES livi_trips(id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('passenger_chat', 'base')),
    sender_role TEXT NOT NULL CHECK (sender_role IN ('passenger', 'driver', 'base')),
    sender_id TEXT,
    body TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 1000),
    media_data TEXT,
    media_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE livre_trip_messages ADD COLUMN IF NOT EXISTS media_data TEXT;
ALTER TABLE livre_trip_messages ADD COLUMN IF NOT EXISTS media_type TEXT;
ALTER TABLE livre_trip_messages ADD COLUMN IF NOT EXISTS handie_session_id UUID;
CREATE INDEX IF NOT EXISTS idx_livre_trip_messages_trip_channel
    ON livre_trip_messages (trip_id, channel, created_at);

CREATE TABLE IF NOT EXISTS livre_handie_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    driver_user_id TEXT NOT NULL REFERENCES livre_users(id),
    trip_id BIGINT REFERENCES livi_trips(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ
);
ALTER TABLE livre_trip_messages ALTER COLUMN trip_id DROP NOT NULL;
ALTER TABLE livre_trip_messages ADD CONSTRAINT livre_trip_messages_handie_fk
    FOREIGN KEY (handie_session_id) REFERENCES livre_handie_sessions(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_livre_handie_sessions_status
    ON livre_handie_sessions (status, created_at);
