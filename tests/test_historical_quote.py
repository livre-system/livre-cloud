from decimal import Decimal

from app.integrations.historical_quote import calculate_quote, historical_options


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
