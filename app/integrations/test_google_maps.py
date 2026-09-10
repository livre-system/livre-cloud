import unittest

from app.integrations.google_maps import GeocodingError, parse_geocode_response


class GoogleMapsParsingTests(unittest.TestCase):
    def test_parse_geocode_response_returns_coordinates_and_formatted_address(self):
        data = {
            "status": "OK",
            "results": [
                {
                    "formatted_address": "Mitre 300, Bahía Blanca, Buenos Aires, Argentina",
                    "geometry": {"location": {"lat": -38.7130496, "lng": -62.267045}},
                }
            ],
        }

        result = parse_geocode_response(data)

        self.assertEqual(result, {
            "latitude": -38.7130496,
            "longitude": -62.267045,
            "formatted_address": "Mitre 300, Bahía Blanca, Buenos Aires, Argentina",
        })

    def test_parse_geocode_response_rejects_ambiguous_results(self):
        data = {
            "status": "OK",
            "results": [
                {"formatted_address": "Mitre 300, Bahía Blanca", "geometry": {"location": {"lat": 1, "lng": 2}}},
                {"formatted_address": "Mitre 300, otra ciudad", "geometry": {"location": {"lat": 3, "lng": 4}}},
            ],
        }

        with self.assertRaisesRegex(GeocodingError, "ambiguos"):
            parse_geocode_response(data)

    def test_parse_geocode_response_rejects_no_results(self):
        with self.assertRaisesRegex(GeocodingError, "no encontrada"):
            parse_geocode_response({"status": "ZERO_RESULTS", "results": []})


if __name__ == "__main__":
    unittest.main()
