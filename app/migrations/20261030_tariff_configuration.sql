-- Configuración vigente de tarifas. No recalcula ni modifica viajes históricos.
CREATE TABLE IF NOT EXISTS livre_tariff_configuration (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    rate_id INTEGER NOT NULL DEFAULT 6243,
    premium_price_per_km NUMERIC(12, 4),
    premium_fixed_cost NUMERIC(12, 2),
    ahorro_discount_percentage NUMERIC(6, 2) NOT NULL DEFAULT 25.00
        CHECK (ahorro_discount_percentage >= 0 AND ahorro_discount_percentage <= 100),
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tariff_premium_price_valid CHECK (premium_price_per_km IS NULL OR premium_price_per_km > 0),
    CONSTRAINT tariff_premium_fixed_valid CHECK (premium_fixed_cost IS NULL OR premium_fixed_cost >= 0)
);

INSERT INTO livre_tariff_configuration (id)
VALUES (1)
ON CONFLICT (id) DO NOTHING;
