"""Geocodificación y distancia vial para cotizaciones locales."""

import os
from urllib.parse import quote_plus

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
GOOGLE_GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GOOGLE_DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"


class GeocodingError(RuntimeError):
    """La dirección o la ruta no pudo resolverse de forma segura."""


def parse_geocode_response(data: dict) -> dict:
    """Extrae coordenadas de una respuesta Google inequívoca."""
    status = data.get("status")
    results = data.get("results") or []

    if status == "ZERO_RESULTS" or not results:
        raise GeocodingError("dirección no encontrada")
    if status != "OK":
        raise GeocodingError(f"error de geocodificación: {status or 'desconocido'}")
    if len(results) != 1:
        raise GeocodingError("resultados ambiguos para la dirección")

    result = results[0]
    location = (result.get("geometry") or {}).get("location") or {}
    if "lat" not in location or "lng" not in location:
        raise GeocodingError("la respuesta no contiene coordenadas")

    return {
        "latitude": float(location["lat"]),
        "longitude": float(location["lng"]),
        "placeId": result.get("place_id"),
        "formatted_address": result.get("formatted_address", ""),
    }


def geocode_address(address: str) -> dict:
    """Convierte una dirección escrita en coordenadas de Bahía Blanca."""
    if not GOOGLE_MAPS_API_KEY:
        raise GeocodingError("GOOGLE_MAPS_API_KEY no configurada")

    query = f"{address}, Bahía Blanca, Buenos Aires, Argentina"
    try:
        import httpx

        response = httpx.get(
            f"{GOOGLE_GEOCODING_URL}?address={quote_plus(query)}&language=es&region=ar&key={GOOGLE_MAPS_API_KEY}",
            timeout=15,
        )
        response.raise_for_status()
        return parse_geocode_response(response.json())
    except GeocodingError:
        raise
    except Exception as exc:
        raise GeocodingError(f"no se pudo geocodificar la dirección: {exc}") from exc


def route_distance_km(origin: dict, destination: dict) -> float:
    """Obtiene distancia vial; no usa MAGIIS ni estima en línea recta."""
    if not GOOGLE_MAPS_API_KEY:
        raise GeocodingError("GOOGLE_MAPS_API_KEY no configurada")
    try:
        import httpx

        params = {
            "origins": f"{origin['latitude']},{origin['longitude']}",
            "destinations": f"{destination['latitude']},{destination['longitude']}",
            "language": "es",
            "region": "ar",
            "key": GOOGLE_MAPS_API_KEY,
        }
        response = httpx.get(GOOGLE_DISTANCE_MATRIX_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "OK":
            raise GeocodingError(f"error de rutas: {data.get('status', 'desconocido')}")
        element = (data.get("rows") or [{}])[0].get("elements", [{}])[0]
        if element.get("status") != "OK":
            raise GeocodingError("no se pudo calcular la distancia vial")
        meters = ((element.get("distance") or {}).get("value"))
        if not meters or meters <= 0:
            raise GeocodingError("la ruta no contiene una distancia válida")
        return meters / 1000
    except GeocodingError:
        raise
    except Exception as exc:
        raise GeocodingError(f"no se pudo calcular la distancia: {exc}") from exc
