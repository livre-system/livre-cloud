from decimal import Decimal

from app.integrations.historical_quote import calculate_quote, historical_options
from app.integrations import google_maps


def test_calculates_price_from_historical_rate_and_distance():
    quote = calculate_quote(
        distance_km=Decimal("2.63"),
        price_per_km=Decimal("1046.5"),
        fixed_cost=Decimal("1399.3"),
    )

    assert quote["price"] == 4152
    assert quote["distance"] == 2.63
    assert quote["source"] == "historical_quotes"


def test_exposes_historical_service_options():
    profiles = [
        {"rate_id": 6243, "transport_type_id": "1", "price_per_km": "1610", "fixed_cost": "2153.1"},
        {"rate_id": 6243, "transport_type_id": "2", "price_per_km": "1046.5", "fixed_cost": "1399.3"},
        {"rate_id": 6243, "transport_type_id": "3", "price_per_km": "1449", "fixed_cost": "1937.8"},
        {"rate_id": 6243, "transport_type_id": "81", "price_per_km": "1610", "fixed_cost": "2153.1"},
    ]
    options = historical_options(Decimal("2.63"), profiles)

    assert [item["transportTypeId"] for item in options] == ["1", "2", "3", "81"]
    assert options[0]["serviceName"] == "Livre Ahorro"
    assert options[1]["serviceName"] == "Livre Premium"
    assert options[1]["pricePointToPoint"] == 4152
    assert all(item["serviceStatus"] == "AVAILABLE" for item in options)


def test_rejects_invalid_distance():
    try:
        calculate_quote(
            distance_km=Decimal("0"),
            price_per_km=Decimal("1046.5"),
            fixed_cost=Decimal("1399.3"),
        )
    except ValueError as exc:
        assert "distance" in str(exc)
    else:
        raise AssertionError("expected invalid distance to fail")


def test_google_zero_results_falls_back_to_nominatim(monkeypatch):
    monkeypatch.setattr(google_maps, "GOOGLE_MAPS_API_KEY", "configured")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ZERO_RESULTS", "results": []}

    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(
        "app.integrations.nominatim.geocode_address",
        lambda address: {"latitude": -38.7, "longitude": -62.2, "placeId": None},
    )

    assert google_maps.geocode_address("Alsina 65")["latitude"] == -38.7


def test_google_route_denied_falls_back_to_osrm(monkeypatch):
    monkeypatch.setattr(google_maps, "GOOGLE_MAPS_API_KEY", "configured")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "REQUEST_DENIED"}

    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(
        google_maps,
        "_osrm_route_distance_km",
        lambda origin, destination: 3.2,
    )

    assert google_maps.route_distance_km(
        {"latitude": -38.7, "longitude": -62.2},
        {"latitude": -38.69, "longitude": -62.21},
    ) == 3.2
