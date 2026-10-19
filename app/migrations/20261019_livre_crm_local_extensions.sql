-- Extensiones locales del CRM. No elimina ni altera datos existentes.
CREATE TABLE IF NOT EXISTS livre_quote_profiles (
    id BIGSERIAL PRIMARY KEY,
    rate_id INTEGER NOT NULL,
    transport_type_id TEXT NOT NULL,
    price_per_km NUMERIC(12, 4) NOT NULL,
    fixed_cost NUMERIC(12, 2) NOT NULL,
    distance NUMERIC(12, 2),
    total_cost_price NUMERIC(12, 2),
    quote_body JSONB NOT NULL DEFAULT '{}'::jsonb,
    source TEXT NOT NULL DEFAULT 'livre-cloud',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_livre_quote_profiles_rate_type
    ON livre_quote_profiles (rate_id, transport_type_id);

-- Importación única de perfiles tarifarios ya guardados: no borra ni modifica
-- las tablas históricas; el runtime leerá sólo livre_quote_profiles.
INSERT INTO livre_quote_profiles
    (rate_id, transport_type_id, price_per_km, fixed_cost, distance, total_cost_price, quote_body, source)
SELECT rate_id,
       transport_type_id,
       (quote_body->>'pricePerKm')::numeric,
       total_cost_price::numeric - distance::numeric * (quote_body->>'pricePerKm')::numeric,
       distance::numeric,
       total_cost_price::numeric,
       quote_body,
       'livre-cloud-imported'
  FROM magiis_quotes
 WHERE distance IS NOT NULL
   AND total_cost_price IS NOT NULL
   AND quote_body->>'pricePerKm' IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM livre_quote_profiles);

CREATE TABLE IF NOT EXISTS livre_crm_commissions (
    id BIGSERIAL PRIMARY KEY,
    trip_id BIGINT REFERENCES livi_trips(id) ON DELETE SET NULL,
    vehicle TEXT,
    vehicle_model TEXT,
    owner TEXT,
    total NUMERIC(12, 2) NOT NULL,
    commission NUMERIC(12, 2) NOT NULL,
    commission_percent NUMERIC(6, 2) NOT NULL,
    settled_at TIMESTAMPTZ,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_livre_crm_commissions_settled_at
    ON livre_crm_commissions (settled_at);
