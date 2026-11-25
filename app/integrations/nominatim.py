"""Geocodificación con OpenStreetMap Nominatim, sin API key."""

import unicodedata
import re

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "LivreCloud-MAGIIS/1.0"


class GeocodingError(RuntimeError):
    """La dirección no pudo resolverse de forma segura."""


def geocode_address(address: str, city: str = "Bahía Blanca") -> dict:
    """Devuelve coordenadas y nombre legible para una dirección única."""
    if not address or not address.strip():
        raise GeocodingError("dirección vacía")

    city_key = "".join(
        char for char in unicodedata.normalize("NFKD", city.lower())
        if not unicodedata.combining(char)
    )
    address_key = "".join(
        char for char in unicodedata.normalize("NFKD", address.lower())
        if not unicodedata.combining(char)
    )
    query = address if city_key in address_key else f"{address}, {city}"

    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={
                "q": f"{query}, Buenos Aires, Argentina",
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 2,
                "countrycodes": "ar",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        results = response.json()
    except GeocodingError:
        raise
    except Exception as exc:
        raise GeocodingError(f"no se pudo geocodificar la dirección: {exc}") from exc

    if not results:
        raise GeocodingError(f"dirección no encontrada: {address}")
    if len(results) > 1:
        # Nominatim puede devolver varios POI en el mismo domicilio (por
        # ejemplo, municipalidad y cajero en Alsina 65). Eso no es una
        # ambigüedad de recorrido: comparten calle y número.
        locations = {
            (
                item.get("address", {}).get("road"),
                item.get("address", {}).get("house_number"),
            )
            for item in results
        }
        locations.discard((None, None))
        if len(locations) != 1:
            raise GeocodingError(f"resultados ambiguos para la dirección: {address}")

    result = results[0]
    requested_number = re.search(r"\b(\d{1,5})\b", address)
    returned_number = re.search(r"\b(\d{1,5})\b", result.get("address", {}).get("house_number", "") or result.get("display_name", ""))
    if requested_number and not returned_number:
        raise GeocodingError("el mapa no confirmó la altura solicitada")
    if requested_number and returned_number and requested_number.group(1) != returned_number.group(1):
        raise GeocodingError("el mapa devolvió otra altura para la dirección")
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
