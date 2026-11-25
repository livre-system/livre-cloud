"""Validación y persistencia común de coordenadas de viajes."""
from app.integrations.google_maps import GeocodingError, geocode_address


def resolve_trip_coordinates(origin: str, destination: str) -> dict[str, float]:
    if not origin or not destination:
        raise GeocodingError("origen y destino son obligatorios")
    origin_geo = geocode_address(origin)
    destination_geo = geocode_address(destination)
    lat_delta = abs(origin_geo["latitude"] - destination_geo["latitude"])
    lng_delta = abs(origin_geo["longitude"] - destination_geo["longitude"])
    if lat_delta < 0.00005 and lng_delta < 0.00005:
        raise GeocodingError("origen y destino quedaron en la misma ubicación")
    return {
        "origen_latitude": origin_geo["latitude"],
        "origen_longitude": origin_geo["longitude"],
        "destino_latitude": destination_geo["latitude"],
        "destino_longitude": destination_geo["longitude"],
    }
