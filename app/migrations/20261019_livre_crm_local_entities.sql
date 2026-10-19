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
