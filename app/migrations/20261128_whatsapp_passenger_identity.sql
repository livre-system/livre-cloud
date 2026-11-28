-- WhatsApp passenger identity additions. This is a new migration because
-- previously applied migrations are immutable and checksum-verified.
ALTER TABLE livre_crm_passengers
    ADD COLUMN IF NOT EXISTS whatsapp_user TEXT;

ALTER TABLE livi_whatsapp_quotes
    ADD COLUMN IF NOT EXISTS passenger_name TEXT;

ALTER TABLE livi_trips
    ADD COLUMN IF NOT EXISTS passenger_name TEXT;

-- Remove historical duplicates before enforcing the phone identity invariant.
DELETE FROM livre_crm_passengers newer
USING livre_crm_passengers older
WHERE newer.phone IS NOT NULL
  AND newer.phone <> ''
  AND newer.phone = older.phone
  AND (COALESCE(newer.updated_at, TIMESTAMPTZ '1970-01-01'), newer.id)
      < (COALESCE(older.updated_at, TIMESTAMPTZ '1970-01-01'), older.id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_livre_crm_passengers_phone
    ON livre_crm_passengers (phone)
    WHERE phone IS NOT NULL AND phone <> '';
