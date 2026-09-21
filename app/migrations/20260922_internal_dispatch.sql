-- Dispatch interno de Livre Cloud.
-- MAGIIS no participa del pedido, asignación ni seguimiento.

ALTER TABLE livre_driver_accounts
    ALTER COLUMN magiis_driver_id DROP NOT NULL;

ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS assigned_driver_user_id TEXT
    REFERENCES livre_users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_livi_trips_assigned_driver
    ON livi_trips (assigned_driver_user_id)
    WHERE assigned_driver_user_id IS NOT NULL;

-- El conductor de pruebas se habilita dentro de Livre, sin identificador externo.
INSERT INTO livre_driver_accounts (user_id, magiis_driver_id, active)
SELECT id, NULL, TRUE
FROM livre_users
WHERE username = 'livreuser'
ON CONFLICT (user_id) DO UPDATE SET active = TRUE;
