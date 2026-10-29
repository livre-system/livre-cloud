-- Perfil estructurado de los choferes creados desde el CRM.
-- Mantiene la cuenta de acceso separada de los datos operativos/personales.
CREATE TABLE IF NOT EXISTS livre_crm_driver_profiles (
    driver_user_id TEXT PRIMARY KEY REFERENCES livre_users(id) ON DELETE CASCADE,
    dni BIGINT NOT NULL,
    cuil TEXT,
    born_date TIMESTAMPTZ,
    genre TEXT,
    city TEXT NOT NULL,
    province TEXT NOT NULL,
    country TEXT NOT NULL DEFAULT 'Argentina',
    phone_number TEXT NOT NULL,
    street_name TEXT,
    street_number TEXT,
    type_of_living_place TEXT,
    floor_number TEXT,
    postal_code TEXT,
    travel_percentage NUMERIC(6, 2) NOT NULL DEFAULT 15,
    cost_percentage NUMERIC(6, 2) NOT NULL DEFAULT 0,
    vehicle_owner_id BIGINT REFERENCES livre_crm_vehicle_owners(id) ON DELETE SET NULL,
    languages JSONB NOT NULL DEFAULT '[]'::jsonb,
    driver_schedule JSONB NOT NULL DEFAULT '[]'::jsonb,
    payment_settings JSONB NOT NULL DEFAULT '[]'::jsonb,
    vehicles JSONB NOT NULL DEFAULT '[]'::jsonb,
    documents JSONB NOT NULL DEFAULT '[]'::jsonb,
    driver_image TEXT,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_livre_crm_driver_profiles_dni
    ON livre_crm_driver_profiles (dni);
CREATE INDEX IF NOT EXISTS idx_livre_crm_driver_profiles_phone
    ON livre_crm_driver_profiles (phone_number);
