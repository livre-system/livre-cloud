from pathlib import Path

ROOT = Path(__file__).parents[1]
MOBILITY = (ROOT / "app/mobility/router.py").read_text()
SCHEMAS = (ROOT / "app/mobility/schemas.py").read_text()
CRM = (ROOT / "app/crm/router.py").read_text()
MIGRATION = (ROOT / "app/migrations/20261005_driver_shift_closures.sql").read_text()


def test_driver_history_is_scoped_to_local_livre_trips_and_has_date_totals():
    assert '@router.get("/driver/history"' in MOBILITY
    assert "assigned_driver_user_id = %s" in MOBILITY
    assert "completed_at" in MOBILITY
    assert "total_revenue" in MOBILITY
    assert "magiis_" not in MOBILITY[MOBILITY.index('@router.get("/driver/history"'):]


def test_shift_closure_is_idempotent_and_traces_trips():
    assert '@router.post("/driver/shift-closures"' in MOBILITY
    assert "livre_driver_shift_closures" in MOBILITY
    assert "livre_driver_shift_closure_trips" in MOBILITY
    assert "hashtext(%s)" in MOBILITY
    assert "already closed" in MOBILITY


def test_shift_closure_includes_completed_and_cancelled_trips_but_revenue_is_completed_only():
    closure = MOBILITY[MOBILITY.index('@router.post("/driver/shift-closures"'):]
    assert closure.count("status IN ('completed', 'cancelled')") >= 2
    assert "status = 'completed'" in closure


def test_pdf_is_generated_by_livre_cloud_without_external_provider():
    assert '@router.get("/driver/shift-closures/{closure_id}/pdf"' in MOBILITY
    assert "application/pdf" in MOBILITY
    assert "Response(" in MOBILITY


def test_crm_can_consult_persisted_driver_closures():
    assert '@router.get("/driver-shift-closures"' in CRM
    assert "livre_driver_shift_closures" in CRM
    assert "livre_driver_shift_closure_trips" in CRM


def test_shift_migration_is_non_destructive_and_unique_per_driver_day():
    assert "CREATE TABLE IF NOT EXISTS livre_driver_shift_closures" in MIGRATION
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in MIGRATION
    assert "driver_user_id, closure_date" in MIGRATION
    assert "CREATE TABLE IF NOT EXISTS livre_driver_shift_closure_trips" in MIGRATION
    assert "ADD COLUMN IF NOT EXISTS completed_at" in MIGRATION


def test_schemas_expose_history_closure_and_vehicle_owner_fields():
    assert "class DriverHistoryResponse" in SCHEMAS
    assert "class DriverShiftClosureCreate" in SCHEMAS
    assert "class DriverShiftClosure" in SCHEMAS
    assert "owner_name" in SCHEMAS
