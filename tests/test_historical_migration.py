from decimal import Decimal

from app.crm.historical_migration import (
    commission_rule,
    commission_split,
    owner_source_audit,
    passenger_classification,
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
