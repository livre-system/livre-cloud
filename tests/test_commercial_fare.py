from decimal import Decimal
import unittest

from app.integrations.historical_quote import apply_whatsapp_discount, commercial_fare


class CommercialFareTests(unittest.TestCase):
    def test_fifteen_percent_is_calculated_once_in_cents(self):
        result = commercial_fare("5054")
        self.assertEqual(result["original_fare"], Decimal("5054.00"))
        self.assertEqual(result["discount_percentage"], Decimal("15.00"))
        self.assertEqual(result["discount_amount"], Decimal("758.10"))
        self.assertEqual(result["final_fare"], Decimal("4295.90"))

    def test_whatsapp_quote_replaces_only_customer_price_and_keeps_snapshot(self):
        quote = {"pricePointToPoint": 5054, "transportTypeId": "2"}
        result = apply_whatsapp_discount(quote)
        self.assertEqual(result["finalFare"], 4295.90)
        self.assertEqual(result["originalFare"], 5054.0)
        self.assertEqual(result["discountAmount"], 758.10)
        self.assertEqual(result["fareSource"], "whatsapp")
        self.assertEqual(result["transportTypeId"], "2")

    def test_whatsapp_discount_is_idempotent(self):
        quote = apply_whatsapp_discount({"pricePointToPoint": 5054})
        reapplied = apply_whatsapp_discount(quote)
        self.assertEqual(reapplied, quote)
        self.assertEqual(reapplied["originalFare"], 5054.0)
        self.assertEqual(reapplied["discountAmount"], 758.10)
        self.assertEqual(reapplied["finalFare"], 4295.90)

    def test_invalid_discount_is_rejected(self):
        with self.assertRaises(ValueError):
            commercial_fare(100, Decimal("101"))


if __name__ == "__main__":
    unittest.main()
