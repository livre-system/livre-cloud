import json
import unittest
from pathlib import Path

from app.integrations.magiis_web import create_travel


FIXTURE = Path(__file__).parent / "fixtures" / "magiis_web_create_travel.json"


class FakeResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {}


class FakeClient:
    def __init__(self):
        self.payload = None

    def request(self, method, path, **kwargs):
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


if __name__ == "__main__":
    unittest.main()
