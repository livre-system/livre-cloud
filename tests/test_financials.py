from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_financial_rule_preserves_gross_and_splits_15_percent():
    from app.finance import calculate_financials

    values = calculate_financials(Decimal("1234.56"))
    assert values == {
        "gross_fare": Decimal("1234.56"),
        "livre_commission": Decimal("185.18"),
        "driver_net": Decimal("1049.38"),
        "card_payment_confirmed": False,
    }


def test_rounding_keeps_gross_equal_commission_plus_net():
    from app.finance import calculate_financials

    values = calculate_financials("100.01")
    assert values["gross_fare"] == values["livre_commission"] + values["driver_net"]


def test_missing_gross_is_pending_and_never_finalized():
    from app.finance import finalize_trip_financials

    class Cursor:
        def __init__(self):
            self.sql = ""
            self.params = None

        def execute(self, sql, params):
            self.sql, self.params = sql, params

        def fetchone(self):
            return None

    cur = Cursor()
    assert finalize_trip_financials(cur, 7) is None
    assert "THEN 'pending_settlement'" in cur.sql
    assert "financial_finalized_at = CASE" in cur.sql


def test_duplicate_and_retry_finalization_are_sql_idempotent():
    from app.finance import finalize_trip_financials

    class Cursor:
        def __init__(self):
            self.sql = ""
            self.params = None
            self.calls = 0

        def execute(self, sql, params):
            self.sql, self.params = sql, params
            self.calls += 1

        def fetchone(self):
            return {"gross_fare": Decimal("100.00"), "livre_commission": Decimal("15.00"), "driver_net": Decimal("85.00"), "financial_status": "finalized"}

    cur = Cursor()
    first = finalize_trip_financials(cur, 7)
    second = finalize_trip_financials(cur, 7)
    assert first is not None and first["driver_net"] == Decimal("85.00")
    assert second is not None and second["driver_net"] == Decimal("85.00")
    assert cur.calls == 2
    assert "COALESCE(financial_finalized_at, NOW())" in cur.sql
    assert "COALESCE(gross_fare, fare)" in cur.sql
    assert "financial_status" in cur.sql


def test_all_current_completion_origins_call_the_same_financial_finalizer():
    sources = {
        "normal": ROOT / "app/mobility/router.py",
        "crm": ROOT / "app/crm/router.py",
        "whatsapp_elevenlabs": ROOT / "app/voice/elevenlabs_trip.py",
    }
    for name, path in sources.items():
        source = path.read_text()
        assert "finalize_trip_financials" in source, name


def test_voice_creation_persists_the_real_gross_or_null_without_inventing():
    source = (ROOT / "app/voice/elevenlabs_trip.py").read_text()
    assert "tracking_token, assigned_driver_user_id" in source
    assert "fare, gross_fare" in source
    assert "fare,\n                fare," in source



def test_web_creation_contract_persists_optional_real_gross_and_never_zero():
    source = (ROOT / "app/mobility/router.py").read_text()
    schema = (ROOT / "app/mobility/schemas.py").read_text()
    assert "fare, gross_fare" in source
    assert "payload.gross_fare or payload.fare" in source
    assert "gt=0" in schema


def test_crm_accepts_only_explicit_positive_provider_fare():
    source = (ROOT / "app/crm/router.py").read_text()
    assert "def _provider_gross_fare" in source
    assert "if amount > 0" in source
    assert "gross_fare = COALESCE(gross_fare, %s)" in source
    assert "financial_status <> 'finalized'" in (ROOT / "app/mobility/router.py").read_text()
    assert "VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s, %s, 'assigned', %s, %s)" in (ROOT / "app/voice/elevenlabs_trip.py").read_text()


def test_financial_schema_backfills_completed_and_marks_missing_pending():
    migration = (ROOT / "app/migrations/20261006_trip_financials.sql").read_text()
    assert "UPDATE livi_trips" in migration
    assert "status = 'completed'" in migration
    assert "card_payment_confirmed = FALSE" in migration
    assert "gross_fare = COALESCE(gross_fare, fare)" in migration
    assert "financial_status = 'pending_settlement'" in migration
    assert "financial_finalized_at = NULL" in migration
