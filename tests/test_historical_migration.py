from decimal import Decimal

from app.crm.historical_migration import (
    commission_rule,
    commission_split,
    financial_audit,
    manual_review_queue,
    owner_source_audit,
    passenger_classification,
    vehicle_migration_plan,
    vehicle_relationship_report,
)


def test_passengers_report_exact_eligible_missing_and_ambiguous_without_merging():
    rows = [
        {"magiis_id": 1, "firstName": "Ana", "lastName": "Paz", "phone": "111"},
        {"magiis_id": 2, "firstName": "Ana", "lastName": "Paz", "phone": "111"},
        {"magiis_id": 3, "firstName": "Luis", "lastName": "Sol", "phone": ""},
        {"magiis_id": 4, "firstName": "Eva", "lastName": "Mar", "phone": "222"},
    ]
    result = passenger_classification(rows)
    assert result["total"] == 4
    assert result["eligible"] == 1
    assert result["ambiguous_records"] == 2
    assert result["missing_phone"] == 1
    assert result["does_not_merge"] is True


def test_passenger_repeated_classification_is_idempotent():
    rows = [{"magiis_id": 8, "firstName": "A", "lastName": "B", "phone": "123"}]
    assert passenger_classification(rows) == passenger_classification(rows)


def test_owner_source_does_not_invent_identity_and_checks_first_party_source():
    owners = [{"magiis_id": 10, "contactEmail": "x@example.test"}, {"magiis_id": 11}]
    result = owner_source_audit(owners, ["x@example.test"])
    assert result["complete_names"] == 0
    assert result["authorized_email_matches"] == 1
    assert result["safe_candidates"] == 0


def test_vehicles_do_not_promote_unresolved_owner_relationships():
    result = vehicle_relationship_report(
        [{"magiis_id": 1, "ownerId": 99}, {"magiis_id": 2}], {10}
    )
    assert result["promotable"] == 0
    assert result["linked_owner_count"] == 0
    assert result["missing_owner"] == 1


def test_commission_is_split_once_15_85_without_settlement():
    split = commission_split("100.00")
    assert split["livre"] == Decimal("15.00")
    assert split["driver"] == Decimal("85.00")
    assert commission_rule()["creates_liquidation"] is False
    assert commission_rule()["marks_paid"] is False


def test_dry_run_has_no_write_statements_or_external_magiis_calls():
    source = open("scripts/historical_crm_dry_run.py").read().upper()
    assert "INSERT INTO" not in source
    assert "UPDATE " not in source
    assert "DELETE FROM" not in source
    assert "HTTPX" not in source
    assert "REQUESTS" not in source


def test_schema_migration_is_additive_and_idempotent_by_unique_source_keys():
    sql = open("docs/migrations/20261019_historical_migration_source_keys.sql").read()
    assert "ADD COLUMN IF NOT EXISTS source_magiis_id" in sql
    assert sql.count("CREATE UNIQUE INDEX IF NOT EXISTS") == 3
    assert "INSERT INTO" not in sql
    assert "DELETE" not in sql
    assert "TRUNCATE" not in sql


def test_manual_review_keeps_ambiguous_and_missing_phone_pending():
    rows = [
        {"magiis_id": 1, "firstName": "A", "lastName": "B", "phone": "1"},
        {"magiis_id": 2, "firstName": "A", "lastName": "B", "phone": "1"},
        {"magiis_id": 3, "firstName": "C", "lastName": "D", "phone": ""},
    ]
    queue = manual_review_queue(rows)
    assert {item["source_magiis_id"] for item in queue} == {1, 2, 3}
    assert all(item["status"] == "pending_manual_review" for item in queue)
    assert all(item["decision"] is None for item in queue)
    assert {item["reason"] for item in queue} == {"ambiguous_identity", "missing_phone"}


def test_financial_audit_requires_exact_split_and_never_marks_pending_paid():
    report = financial_audit([
        {"id": 10, "gross_fare": "100", "livre_commission": "15", "driver_net": "85", "financial_status": "finalized", "payment_confirmed": False},
        {"id": 11, "gross_fare": "100", "livre_commission": "15", "driver_net": "85", "financial_status": "pending_settlement", "payment_confirmed": False},
    ])
    assert report["count"] == 2
    assert report["closing_rows"] == 2
    assert report["paid_rows"] == 0
    assert report["pending_rows"] == 1


def test_vehicle_plan_keeps_owner_source_key_before_local_fk():
    plan = vehicle_migration_plan(
        [{"magiis_id": 20, "ownerId": 7, "identificationCard": {"domain": "AA", "mark": "X", "model": "Y"}}],
        {7},
    )
    assert plan[0]["source_magiis_id"] == 20
    assert plan[0]["owner_source_magiis_id"] == 7
    assert plan[0]["status"] == "blocked_until_owner_resolution"
    assert plan[0]["local_owner_id"] is None
