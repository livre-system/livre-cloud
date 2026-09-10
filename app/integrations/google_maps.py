"""Geocodificación de direcciones para enviar coordenadas a MAGIIS."""

import os
from urllib.parse import quote_plus


GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
GOOGLE_GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"


class GeocodingError(RuntimeError):
    """La dirección no pudo resolverse de forma segura."""


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
