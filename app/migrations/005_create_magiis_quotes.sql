-- Cotizaciones Web de MAGIIS asociadas a una solicitud concreta de LIVI.
-- whatsapp_user es la identidad del pasajero de LIVI; no referencia livre_users.

CREATE TABLE IF NOT EXISTS magiis_quote_requests (
    id                  BIGSERIAL PRIMARY KEY,
    livi_trip_id        INTEGER NOT NULL REFERENCES livi_trips(id) ON DELETE CASCADE,
    whatsapp_user       TEXT NOT NULL,
    http_status         INTEGER NOT NULL,
    request_payload     JSONB NOT NULL,
    response_body       JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_magiis_quote_requests_trip
    ON magiis_quote_requests (livi_trip_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_magiis_quote_requests_whatsapp
    ON magiis_quote_requests (whatsapp_user, created_at DESC);

CREATE TABLE IF NOT EXISTS magiis_quotes (
    id                  BIGSERIAL PRIMARY KEY,
    quote_request_id    BIGINT NOT NULL REFERENCES magiis_quote_requests(id) ON DELETE CASCADE,
    transport_type_id   INTEGER,
    vehicle_type_id     INTEGER,
    service_type_id     TEXT,
    service_status      TEXT,
    price_point_to_point NUMERIC,
    total_cost_price    NUMERIC,
    distance            NUMERIC,
    duration            TEXT,
    rate_id             INTEGER,
    quote_body          JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_magiis_quotes_request
    ON magiis_quotes (quote_request_id);

CREATE INDEX IF NOT EXISTS idx_magiis_quotes_transport
    ON magiis_quotes (transport_type_id);
