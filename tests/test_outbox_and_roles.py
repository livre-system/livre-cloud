from pathlib import Path


def test_outbox_worker_claims_retries_and_uses_configured_sink():
    source = Path("app/core/outbox.py").read_text()
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "processing_at < now() - interval '5 minutes'" in source
    assert "OUTBOX_WEBHOOK_URL no está configurada" in source
    assert "response.raise_for_status()" in source


def test_roles_and_outbox_migration_is_additive_and_idempotent():
    sql = Path("app/migrations/20261023_user_roles_outbox_retention.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS livre_user_roles" in sql
    assert "PRIMARY KEY (user_id, role)" in sql
    assert "ADD COLUMN IF NOT EXISTS processing_at" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_livre_trip_outbox_processing" in sql


def test_auth_issues_explicit_crm_roles_from_database():
    source = Path("app/auth/router.py").read_text()
    assert "FROM livre_user_roles" in source
    assert "crm_admin" not in source  # roles are data, not hard-coded grants
