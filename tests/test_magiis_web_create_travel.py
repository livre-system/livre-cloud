import json
import unittest
from pathlib import Path

from app.integrations.magiis_web import cancel_travel, create_travel, get_place


FIXTURE = Path(__file__).parent / "fixtures" / "magiis_web_create_travel.json"


class FakeResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {}


class FakeClient:
    def __init__(self, response=None):
        self.payload = None
        self.method = None
        self.path = None
        self.response = response or FakeResponse()

    def request(self, method, path, **kwargs):
        self.method = method
        self.path = path
        self.payload = kwargs.get("json")
        return self.response


class PlaceResponse:
    status_code = 200
    text = "[]"

    def json(self):
        return [{
            "shortName": "Dorrego 17, Bahía Blanca",
            "formattedAddress": "Dorrego 17, Bahía Blanca",
            "place_id": "PLACE-DORREGO",
            "geometry": {"location": {"lat": "-38.71445", "lng": "-62.26129"}},
        }]


class CreateTravelPayloadTest(unittest.TestCase):
    def test_payload_matches_successful_har_contract(self):
        expected = json.loads(FIXTURE.read_text())
        client = FakeClient()

        create_travel(
            session={"token": "test"},
            origin=expected["origin"],
            destination=expected["destination"],
            passenger_user_id=expected["passengerUserId"],
            operator_user_id=expected["operatorUserId"],
            service_type_id=expected["serviceTypeId"],
            client=client,
        )
        payload = client.payload

        self.assertEqual(payload, expected)
        self.assertEqual(len(json.dumps(payload, separators=(",", ":"))), 2665)

    def test_get_place_does_not_replace_confirmed_address_with_magiis_label(self):
        place, _ = get_place(
            session={"token": "test"},
            latitude="-38.7144564",
            longitude="-62.2612977",
            client=FakeClient(PlaceResponse()),
        )

        self.assertEqual(place["latitude"], "-38.71445")
        self.assertEqual(place["longitude"], "-62.26129")
        self.assertEqual(place["placeId"], "PLACE-DORREGO")
        self.assertEqual(place["shortName"], "")

    def test_payload_keeps_confirmed_dorrego_25_short_name(self):
        client = FakeClient()
        create_travel(
            session={"token": "test"},
            origin={
                "latitude": "-38.718",
                "longitude": "-62.266",
                "placeId": "PLACE-ORIGIN",
                "shortName": "estomba 550",
            },
            destination={
                "latitude": "-38.71445",
                "longitude": "-62.26129",
                "placeId": "PLACE-DORREGO",
                "shortName": "dorrego 25",
            },
            client=client,
        )

        self.assertEqual(client.payload["destination"]["shortName"], "dorrego 25")

    def test_cancel_payload_targets_exact_travel(self):
        client = FakeClient()
        cancel_travel(
            session={"token": "test"},
            travel_id=98765,
            operator_name="Livre",
            reason="Pedido por pasajero",
            client=client,
        )
        self.assertEqual(client.path, "/carriers/2421/travels/98765/cancel")
        self.assertEqual(client.method, "PUT")
        self.assertEqual(client.payload["travelId"], 98765)
        self.assertEqual(client.payload["reasonForCancellation"], "Pedido por pasajero")


if __name__ == "__main__":
    unittest.main()
