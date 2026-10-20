-- Índice de lectura para detectar liquidaciones existentes por viaje.
-- No impone unicidad sobre datos históricos que podrían contener duplicados.
CREATE INDEX IF NOT EXISTS idx_livre_crm_commissions_trip_id
    ON livre_crm_commissions (trip_id)
    WHERE trip_id IS NOT NULL;
