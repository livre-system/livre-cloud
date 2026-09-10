"""Geocodificación con OpenStreetMap Nominatim, sin API key."""

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "LivreCloud-MAGIIS/1.0"


class GeocodingError(RuntimeError):
    """La dirección no pudo resolverse de forma segura."""


def geocode_address(address: str, city: str = "Bahía Blanca") -> dict:
    """Devuelve coordenadas y nombre legible para una dirección única."""
    if not address or not address.strip():
        raise GeocodingError("dirección vacía")

    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={
                "q": f"{address}, {city}, Buenos Aires, Argentina",
                "format": "jsonv2",
                "limit": 2,
                "countrycodes": "ar",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        response.raise_for_status()
        results = response.json()
    except GeocodingError:
        raise
    except Exception as exc:
        raise GeocodingError(f"no se pudo geocodificar la dirección: {exc}") from exc

    if not results:
        raise GeocodingError(f"dirección no encontrada: {address}")
    if len(results) != 1:
        raise GeocodingError(f"resultados ambiguos para la dirección: {address}")

    result = results[0]
    try:
        latitude = float(result["lat"])
        longitude = float(result["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GeocodingError("la respuesta no contiene coordenadas válidas") from exc

    return {
        "latitude": latitude,
        "longitude": longitude,
        "placeId": None,
        "shortName": result.get("display_name", address),
        "additionalInfo": None,
        "waitTime": None,
    }
