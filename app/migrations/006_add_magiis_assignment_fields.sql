-- Datos de asignación devueltos por MAGIIS para cada viaje local.
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_driver_id BIGINT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_driver_first_name TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_driver_last_name TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_vehicle_domain TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_vehicle_mark TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_vehicle_model TEXT;
ALTER TABLE livi_trips ADD COLUMN IF NOT EXISTS magiis_car_plate TEXT;
