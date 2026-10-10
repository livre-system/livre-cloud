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
            return {"gross_fare": Decimal("100.00"), "livre_commission": Decimal("15.00"), "driver_net": Decimal("85.00")}

    cur = Cursor()
    first = finalize_trip_financials(cur, 7)
    second = finalize_trip_financials(cur, 7)
    assert first is not None and first["driver_net"] == Decimal("85.00")
    assert second is not None and second["driver_net"] == Decimal("85.00")
    assert cur.calls == 2
    assert "COALESCE(financial_finalized_at, NOW())" in cur.sql
    assert "COALESCE(gross_fare, fare)" in cur.sql
    assert "status = 'completed'" in cur.sql


def test_all_current_completion_origins_call_the_same_financial_finalizer():
    sources = {
        "normal": ROOT / "app/mobility/router.py",
        "crm": ROOT / "app/crm/router.py",
        "whatsapp_elevenlabs": ROOT / "app/voice/elevenlabs_trip.py",
    }
    for name, path in sources.items():
        source = path.read_text()
        assert "finalize_trip_financials" in source, name


def test_financial_schema_backfills_completed_trips_and_preserves_card_unknown():
    migration = (ROOT / "app/migrations/20261006_trip_financials.sql").read_text()
    assert "UPDATE livi_trips" in migration
    assert "status = 'completed'" in migration
    assert "card_payment_confirmed = FALSE" in migration
    assert "gross_fare = COALESCE(gross_fare, fare)" in migration
