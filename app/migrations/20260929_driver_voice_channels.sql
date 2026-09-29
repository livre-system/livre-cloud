-- Canales propios de Livre para chat de viaje y comunicación con base.
-- No comunica directamente con MAGIIS ni expone credenciales externas.
CREATE TABLE IF NOT EXISTS livre_trip_messages (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT NOT NULL REFERENCES livi_trips(id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('passenger_chat', 'base')),
    sender_role TEXT NOT NULL CHECK (sender_role IN ('passenger', 'driver', 'base')),
    sender_id TEXT,
    body TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 1000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
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
CREATE INDEX IF NOT EXISTS idx_livre_handie_sessions_status
    ON livre_handie_sessions (status, created_at);
