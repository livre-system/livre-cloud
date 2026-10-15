from decimal import Decimal
import unittest

from app.integrations.historical_quote import apply_commercial_discount, commercial_fare, historical_options


class CommercialFareTests(unittest.TestCase):
    def test_fifteen_percent_is_calculated_once_in_cents(self):
        result = commercial_fare("5054")
        self.assertEqual(result["original_fare"], Decimal("5054.00"))
        self.assertEqual(result["discount_percentage"], Decimal("15.00"))
        self.assertEqual(result["discount_amount"], Decimal("758.10"))
        self.assertEqual(result["final_fare"], Decimal("4295.90"))

    def test_any_new_quote_gets_discount_and_keeps_snapshot(self):
        quote = {"pricePointToPoint": 5054, "transportTypeId": "2"}
        result = apply_commercial_discount(quote)
        self.assertEqual(result["finalFare"], 4295.90)
        self.assertEqual(result["originalFare"], 5054.0)
        self.assertEqual(result["discountAmount"], 758.10)
        self.assertEqual(result["fareSource"], "commercial")
        self.assertEqual(result["transportTypeId"], "2")

    def test_commercial_discount_is_idempotent(self):
        quote = apply_commercial_discount({"pricePointToPoint": 5054})
        reapplied = apply_commercial_discount(quote)
        self.assertEqual(reapplied, quote)
        self.assertEqual(reapplied["originalFare"], 5054.0)
        self.assertEqual(reapplied["discountAmount"], 758.10)
        self.assertEqual(reapplied["finalFare"], 4295.90)

    def test_historical_options_are_commercial_for_every_channel(self):
        options = historical_options(Decimal("10"), profiles=[{
            "rate_id": 6243,
            "transport_type_id": "2",
            "price_per_km": "100",
            "fixed_cost": "54",
        }])
        self.assertEqual(options[0]["originalFare"], 1054.0)
        self.assertEqual(options[0]["discountPercentage"], 15.0)
        self.assertEqual(options[0]["finalFare"], 895.9)
        self.assertEqual(options[0]["fareSource"], "commercial")

    def test_invalid_discount_is_rejected(self):
        with self.assertRaises(ValueError):
            commercial_fare(100, Decimal("101"))


if __name__ == "__main__":
    unittest.main()
