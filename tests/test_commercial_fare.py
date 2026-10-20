from datetime import date
from decimal import Decimal
import unittest

from app.integrations.historical_quote import apply_commercial_discount, commercial_fare, historical_options
from app.mobility.router import _driver_fare, _history_response


class HistoryCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, query, params):
        self.query = (query, params)

    def fetchall(self):
        return self.rows


class CommercialFareTests(unittest.TestCase):
    def test_fifteen_percent_is_calculated_once_in_cents(self):
        result = commercial_fare("5054")
        self.assertEqual(result["original_fare"], Decimal("5054.00"))
        self.assertEqual(result["discount_percentage"], Decimal("15.00"))
        self.assertEqual(result["discount_amount"], Decimal("758.10"))
        self.assertEqual(result["final_fare"], Decimal("4295.90"))

    def test_any_new_quote_gets_discount_and_keeps_snapshot(self):
        quote = {"pricePointToPoint": 5054, "transportTypeId": "1"}
        result = apply_commercial_discount(quote)
        self.assertEqual(result["price"], 5054)
        self.assertEqual(result["pricePointToPoint"], 5054)
        self.assertEqual(result["finalFare"], 4295.90)
        self.assertEqual(result["originalFare"], 5054.0)
        self.assertEqual(result["discountAmount"], 758.10)
        self.assertEqual(result["fareSource"], "commercial")
        self.assertEqual(result["transportTypeId"], "1")

    def test_livre_ahorro_has_no_commission_and_driver_gets_gross(self):
        result = apply_commercial_discount({"pricePointToPoint": 5054, "transportTypeId": "2"})
        self.assertEqual(result["originalFare"], 5054.0)
        self.assertEqual(result["discountPercentage"], 0.0)
        self.assertEqual(result["discountAmount"], 0.0)
        self.assertEqual(result["finalFare"], 5054.0)

    def test_other_service_keeps_fifteen_percent_commission(self):
        result = apply_commercial_discount({"pricePointToPoint": 5054, "transportTypeId": "1"})
        self.assertEqual(result["discountPercentage"], 15.0)
        self.assertEqual(result["discountAmount"], 758.10)
        self.assertEqual(result["finalFare"], 4295.90)

    def test_livre_ahorro_discount_is_idempotent(self):
        quote = apply_commercial_discount({"pricePointToPoint": 5054, "transportTypeId": "2"})
        reapplied = apply_commercial_discount(quote)
        self.assertEqual(reapplied["discountPercentage"], 0.0)
        self.assertEqual(reapplied["discountAmount"], 0.0)
        self.assertEqual(reapplied["finalFare"], 5054.0)

    def test_commercial_discount_is_idempotent(self):
        quote = apply_commercial_discount({"pricePointToPoint": 5054})
        reapplied = apply_commercial_discount(quote)
        self.assertEqual(reapplied, quote)
        self.assertEqual(reapplied["originalFare"], 5054.0)
        self.assertEqual(reapplied["discountAmount"], 758.10)
        self.assertEqual(reapplied["finalFare"], 4295.90)

    def test_historical_options_keep_the_official_fare_for_the_passenger(self):
        options = historical_options(Decimal("10"), profiles=[{
            "rate_id": 6243,
            "transport_type_id": "2",
            "price_per_km": "100",
            "fixed_cost": "54",
        }])
        self.assertEqual(options[0]["pricePointToPoint"], 1054)
        self.assertNotIn("finalFare", options[0])
        self.assertNotIn("discountPercentage", options[0])
        self.assertNotIn("discountAmount", options[0])
        self.assertNotIn("fareSource", options[0])

    def test_driver_gets_exactly_85_percent_from_original_pending_fare(self):
        trip = {"status": "assigned", "original_fare": Decimal("3200.00"), "final_fare": Decimal("2720.00")}
        self.assertEqual(_driver_fare(trip), 2720.0)

    def test_driver_keeps_85_percent_after_completion(self):
        trip = {"status": "completed", "original_fare": Decimal("3200.00"), "final_fare": Decimal("2720.00")}
        self.assertEqual(_driver_fare(trip), 2720.0)

    def test_driver_uses_ahorro_snapshot_without_second_discount(self):
        trip = {
            "status": "assigned", "original_fare": Decimal("3200.00"),
            "final_fare": Decimal("3200.00"), "discount_percentage": Decimal("0.00"),
        }
        self.assertEqual(_driver_fare(trip), 3200.0)

    def test_completed_legacy_trip_without_snapshot_keeps_historical_amount(self):
        trip = {
            "status": "completed", "original_fare": Decimal("3200.00"),
            "fare": Decimal("2720.00"), "final_fare": None,
        }
        self.assertEqual(_driver_fare(trip), 3200.0)

    def test_new_trip_with_snapshot_keeps_persisted_driver_amount(self):
        trip = {
            "status": "completed", "original_fare": Decimal("3200.00"),
            "fare": Decimal("3200.00"), "final_fare": Decimal("2720.00"),
        }
        self.assertEqual(_driver_fare(trip), 2720.0)

    def test_history_uses_85_percent_for_completed_trip_and_keeps_gross_fare(self):
        row = {
            "id": 1, "origen": "A", "destino": "B", "status": "completed",
            "completed_at": None, "fare": Decimal("3200.00"),
            "original_fare": Decimal("3200.00"), "discount_percentage": None,
            "discount_amount": None, "final_fare": Decimal("2720.00"), "fare_source": "commercial",
        }
        result = _history_response(HistoryCursor([row]), "driver-1", date(2026, 10, 19), date(2026, 10, 19))
        self.assertEqual(result["total_revenue"], Decimal("3200.00"))
        self.assertEqual(result["driver_revenue"], Decimal("2720.00"))
        self.assertEqual(result["commission"], Decimal("480.00"))
        self.assertEqual(result["items"][0]["fare"], Decimal("3200.00"))
        self.assertEqual(result["items"][0]["driver_fare"], 2720.0)
        self.assertEqual(result["items"][0]["final_fare"], Decimal("2720.00"))

    def test_history_preserves_completed_legacy_fare_without_snapshot(self):
        rows = [
            {
                "id": 2, "origen": "A", "destino": "B", "status": "completed",
                "completed_at": None, "fare": Decimal("100.00"),
                "original_fare": Decimal("3200.00"), "discount_percentage": None,
                "discount_amount": None, "final_fare": Decimal("2720.00"), "fare_source": "commercial",
            },
            {
                "id": 3, "origen": "A", "destino": "B", "status": "completed",
                "completed_at": None, "fare": Decimal("3200.00"),
                "original_fare": None, "discount_percentage": None,
                "discount_amount": None, "final_fare": None, "fare_source": None,
            },
        ]
        result = _history_response(HistoryCursor(rows), "driver-1", date(2026, 10, 19), date(2026, 10, 19))
        self.assertEqual([item["driver_fare"] for item in result["items"]], [2720.0, 3200.0])
        self.assertEqual(result["total_revenue"], Decimal("6400.00"))
        self.assertEqual(result["driver_revenue"], Decimal("5920.00"))
        self.assertEqual(result["commission"], Decimal("480.00"))

    def test_invalid_discount_is_rejected(self):
        with self.assertRaises(ValueError):
            commercial_fare(100, Decimal("101"))


if __name__ == "__main__":
    unittest.main()
