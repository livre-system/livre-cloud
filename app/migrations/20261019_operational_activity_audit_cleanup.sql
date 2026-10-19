-- Remove defaults that could classify historical trips implicitly.
-- Restore only the values introduced by the previous defaults; preserve explicit
-- real/test (and any other non-default) classifications.
ALTER TABLE livi_trips
    ALTER COLUMN activity_type DROP NOT NULL,
    ALTER COLUMN source DROP NOT NULL,
    ALTER COLUMN activity_type DROP DEFAULT,
    ALTER COLUMN source DROP DEFAULT;

UPDATE livi_trips
   SET activity_type = NULL,
       source = NULL
 WHERE activity_type = 'unclassified'
   AND source = 'unknown';
