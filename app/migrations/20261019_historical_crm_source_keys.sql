-- Claves de idempotencia para el backfill histórico. Sólo estructura.
-- No copia datos ni toca las tablas magiis_crm_*.
ALTER TABLE livre_crm_passengers
    ADD COLUMN IF NOT EXISTS source_magiis_id BIGINT;
ALTER TABLE livre_crm_vehicle_owners
    ADD COLUMN IF NOT EXISTS source_magiis_id BIGINT;
ALTER TABLE livre_crm_vehicles
    ADD COLUMN IF NOT EXISTS source_magiis_id BIGINT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_livre_crm_passengers_source_magiis
    ON livre_crm_passengers (source_magiis_id)
    WHERE source_magiis_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_livre_crm_vehicle_owners_source_magiis
    ON livre_crm_vehicle_owners (source_magiis_id)
    WHERE source_magiis_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_livre_crm_vehicles_source_magiis
    ON livre_crm_vehicles (source_magiis_id)
    WHERE source_magiis_id IS NOT NULL;
