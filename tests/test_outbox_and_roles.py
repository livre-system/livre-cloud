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


def test_role_changes_have_an_audit_trail():
    sql = Path("app/migrations/20261024_migration_checksums.sql").read_text()
    source = Path("app/crm/router.py").read_text()
    assert "CREATE TABLE IF NOT EXISTS livre_user_role_audit" in sql
    assert "idx_livre_user_role_audit_target_time" in sql
    assert "INSERT INTO livre_user_role_audit" in source
    assert "previous_roles" in source


def test_crm_authorization_reads_current_roles_from_database():
    source = Path("app/crm/router.py").read_text()
    assert "def _live_roles" in source
    assert "FROM livre_users u" in source
    assert "LEFT JOIN livre_user_roles" in source
    assert "el JWT solo aporta la identidad" in source
    assert "No tenés permiso para ver recorridos" in source
    assert "{\"crm_admin\", \"admin\"}" in source


def test_runtime_defaults_are_safe_and_readiness_is_separate():
    config = Path("app/core/config.py").read_text()
    main = Path("app/main.py").read_text()
    assert 'os.getenv("AUTO_MIGRATE", "false")' in config
    assert "validate_runtime_config()" in main
    assert '@app.get("/ready")' in main
    assert "schema_migrations" in main
