import asyncio
import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from app.integrations.historical_quote import apply_commercial_discount, commercial_fare, historical_options
from app.mobility.router import _driver_trip, request_trip
from app.mobility.schemas import TripRequest
from app.voice import elevenlabs_trip


class _FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class _FakeCursor:
    def __init__(self):
        self.executed = []
        self.returned = {"id": 901, "tracking_token": "token-901", "status": "assigned"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params):
        self.executed.append((query, params))

    def fetchone(self):
        return self.returned




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
        self.assertEqual(result["pricePointToPoint"], 5054)

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
        self.assertNotIn("fareSource", options[0])

    def test_invalid_discount_is_rejected(self):
        with self.assertRaises(ValueError):
            commercial_fare(100, Decimal("101"))

    def test_quote_endpoint_applies_discount_before_persisting_and_returning(self):
        cursor = _FakeCursor()
        raw_options = [
            {
                "pricePointToPoint": 5054,
                "price": 5054,
                "transportTypeId": "1",
                "serviceName": "Livre Premium",
            },
            {
                "pricePointToPoint": 3332,
                "price": 3332,
                "transportTypeId": "2",
                "serviceName": "Livre Ahorro",
            },
            {
                "pricePointToPoint": 9999,
                "price": 9999,
                "transportTypeId": "3",
                "serviceName": "Otro servicio",
            },
        ]
        request = _FakeRequest({
            "parameters": {
                "origen": "Municipalidad de Bahía Blanca",
                "destino": "Universidad Nacional del Sur",
                "conversation_id": "quote-test-1",
            }
        })

        with patch.object(elevenlabs_trip, "geocode_address", side_effect=[
            {"latitude": -38.7184, "longitude": -62.2639},
            {"latitude": -38.7014, "longitude": -62.2693},
        ]) as geocode, patch.object(
            elevenlabs_trip, "route_distance_km", return_value=2.63
        ) as route, patch.object(
            elevenlabs_trip, "historical_options", return_value=raw_options
        ), patch.object(elevenlabs_trip, "get_cursor", return_value=cursor):
            result = asyncio.run(elevenlabs_trip.elevenlabs_quote(request))

        self.assertEqual(geocode.call_count, 2)
        route.assert_called_once()
        self.assertEqual([item["transportTypeId"] for item in result["options"]], ["1", "2"])
        self.assertEqual(
            [(item["originalFare"], item["discountAmount"], item["finalFare"])
             for item in result["options"]],
            [(5054.0, 758.10, 4295.90), (3332.0, 499.80, 2832.20)],
        )
        self.assertTrue(all(item["discountPercentage"] == 15.0 for item in result["options"]))
        self.assertTrue(all(item["fareSource"] == "commercial" for item in result["options"]))
        self.assertEqual(result["options"][0]["price"], 5054)
        self.assertEqual(result["options"][1]["price"], 3332)

        self.assertEqual(len(cursor.executed), 1)
        persisted_options = json.loads(cursor.executed[0][1][-1])
        self.assertEqual(persisted_options, result["options"])

    def test_creation_and_driver_read_use_the_same_single_discount(self):
        cursor = _FakeCursor()
        with patch.object(elevenlabs_trip, "get_cursor", return_value=cursor):
            trip_id, _ = elevenlabs_trip._save_trip(
                conversation_id="e2e-1",
                origen="Bahía Blanca",
                destino="Ingeniero White",
                whatsapp_user="user-e2e",
                tool_call_id="tool-e2e",
                service_type_id="2",
                service_type_name="Ahorro",
                fare=Decimal("5054.00"),
                original_fare=Decimal("5054.00"),
                discount_percentage=Decimal("0"),
                discount_amount=Decimal("0"),
                final_fare=Decimal("4295.90"),
                fare_source="whatsapp",
            )

        params = cursor.executed[0][1]
        assert trip_id == 901
        assert params[8] == Decimal("5054.00")
        assert params[10] == Decimal("5054.00")
        assert params[11] == Decimal("15.00")
        assert params[12] == Decimal("758.10")
        assert params[13] == Decimal("4295.90")
        assert params[14] == "commercial"

        driver_trip = _driver_trip({
            "id": trip_id,
            "origen": "Bahía Blanca",
            "destino": "Ingeniero White",
            "status": "assigned",
            "tracking_driver_name": None,
            "tracking_plate": None,
            "tracking_vehicle_model": None,
            "tracking_vehicle_color": None,
            "tracking_latitude": None,
            "tracking_longitude": None,
            "tracking_location_updated_at": None,
            "service_type_id": 2,
            "service_type_name": "Ahorro",
            "fare": params[8],
            "original_fare": params[10],
            "discount_percentage": params[11],
            "discount_amount": params[12],
            "final_fare": params[13],
            "fare_source": params[14],
        })
        assert driver_trip.fare == 5054.0
        assert driver_trip.original_fare == 5054.0
        assert driver_trip.final_fare == 4295.9
        assert driver_trip.driver_fare == 4295.9
        assert driver_trip.fare_source == "commercial"
    def test_request_channel_normalizes_an_official_fare_once(self):
        cursor = _FakeCursor()
        response = request_trip(
            TripRequest(
                origin="Bahía Blanca",
                destination="Ingeniero White",
                fare=Decimal("5054.00"),
                service_type_id=2,
            ),
            cur=cursor,
        )
        params = cursor.executed[0][1]
        assert response.trip_id == 901
        assert params[-6:] == (
            Decimal("5054.00"),
            Decimal("5054.00"),
            Decimal("15.00"),
            Decimal("758.10"),
            Decimal("4295.90"),
            "commercial",
        )


if __name__ == "__main__":
    unittest.main()
