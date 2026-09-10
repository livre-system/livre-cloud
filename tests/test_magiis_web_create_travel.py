import json
import unittest
from pathlib import Path

from app.integrations.magiis_web import cancel_travel, create_travel


FIXTURE = Path(__file__).parent / "fixtures" / "magiis_web_create_travel.json"


class FakeResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {}


class FakeClient:
    def __init__(self):
        self.payload = None
        self.method = None
        self.path = None

    def request(self, method, path, **kwargs):
        self.method = method
        self.path = path
        self.payload = kwargs["json"]
        return FakeResponse()


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
