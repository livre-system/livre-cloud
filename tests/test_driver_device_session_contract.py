from pathlib import Path


ROOT = Path(__file__).parents[1]
AUTH = (ROOT / "app/auth/router.py").read_text()
MOBILITY = (ROOT / "app/mobility/router.py").read_text()
SCHEMAS = (ROOT / "app/mobility/schemas.py").read_text()
CRM = (ROOT / "app/crm/router.py").read_text()
MIGRATION = (ROOT / "app/migrations/20261004_driver_device_session.sql").read_text()


def test_driver_login_requires_a_device_id_and_replaces_the_device_owner():
    assert "device_id: str | None = None" in AUTH
    assert "device_id es obligatorio para iniciar sesión como conductor" in AUTH
    assert "pg_advisory_xact_lock(hashtext(%s))" in AUTH
    assert "WHERE active_device_id = %s AND device_session_active = TRUE" in AUTH
    assert "SET device_session_active = TRUE" in AUTH
    assert "active_device_id = %s" in AUTH


def test_gps_requires_the_current_device_session_and_payload_identity():
    assert "device_id: str" in SCHEMAS
    assert "device_id = _active_device_session(cur, user, payload.device_id)" in MOBILITY
    assert "AND active_device_id = %s" in MOBILITY
    assert "AND device_session_active = TRUE" in MOBILITY


def test_logout_cannot_clear_a_replacement_session():
    assert "_active_device_session(cur, user, device_id, require_fresh=False)" in MOBILITY
    assert "WHERE user_id = %s AND active = TRUE" in MOBILITY
    assert "AND (%s IS NULL OR active_device_id = %s)" in MOBILITY
    assert "device_session_active = CASE WHEN %s IS NULL THEN device_session_active ELSE FALSE END" in MOBILITY


def test_operational_presence_uses_only_fresh_device_sessions():
    assert "a.device_session_active = TRUE" in CRM
    assert "a.device_last_seen_at >= NOW() - INTERVAL '2 minutes'" in CRM
    assert "CASE WHEN a.active = TRUE" in CRM
    assert "COALESCE(a.current_latitude, active_trip.tracking_latitude)" not in CRM
    assert "COALESCE(a.current_longitude, active_trip.tracking_longitude)" not in CRM
    assert '"sessionActive": bool(row["session_active"])' in CRM


def test_session_migration_is_non_destructive_idempotent_and_unique():
    assert "ADD COLUMN IF NOT EXISTS active_device_id TEXT" in MIGRATION
    assert "ADD COLUMN IF NOT EXISTS device_session_active BOOLEAN NOT NULL DEFAULT FALSE" in MIGRATION
    assert "ADD COLUMN IF NOT EXISTS device_last_seen_at TIMESTAMPTZ" in MIGRATION
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in MIGRATION
    assert "WHERE device_session_active = TRUE" in MIGRATION
    assert "active_device_id IS NOT NULL" in MIGRATION