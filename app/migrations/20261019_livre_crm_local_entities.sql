-- Entidades propias del CRM de Livre Cloud.
-- No modifica ni elimina tablas históricas de proveedores.
CREATE TABLE IF NOT EXISTS livre_crm_passengers (
    id BIGSERIAL PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT,
    phone TEXT NOT NULL,
    country TEXT NOT NULL DEFAULT 'Argentina',
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_livre_crm_passengers_search
    ON livre_crm_passengers (lower(last_name), lower(first_name), phone);

CREATE TABLE IF NOT EXISTS livre_crm_vehicle_owners (
    id BIGSERIAL PRIMARY KEY,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    identification TEXT,
    email TEXT,
    phone TEXT,
    city TEXT,
    province TEXT,
    street TEXT,
    street_number TEXT,
    notes TEXT,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_livre_crm_vehicle_owners_search
    ON livre_crm_vehicle_owners (lower(last_name), lower(first_name), phone);

CREATE TABLE IF NOT EXISTS livre_crm_vehicles (
    id BIGSERIAL PRIMARY KEY,
    plate TEXT NOT NULL UNIQUE,
    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    year INTEGER NOT NULL,
    color TEXT NOT NULL,
    chassis TEXT,
    motor TEXT,
    vehicle_type TEXT NOT NULL,
    owner_id BIGINT REFERENCES livre_crm_vehicle_owners(id) ON DELETE SET NULL,
    image_url TEXT,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_livre_crm_vehicles_search
    ON livre_crm_vehicles (lower(plate), lower(brand), lower(model));

-- Las cuentas nuevas de Livre no requieren un identificador de proveedor.
ALTER TABLE livre_driver_accounts
    ALTER COLUMN magiis_driver_id DROP NOT NULL;

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

-- Copia una sola vez los perfiles tarifarios ya almacenados para que la
-- cotización runtime lea exclusivamente la tabla propia de Livre.
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
