"""Geocodificación y distancia vial para cotizaciones locales."""

import os
import re
from urllib.parse import quote_plus

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
GOOGLE_GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GOOGLE_DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"
OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving"


class GeocodingError(RuntimeError):
    """La dirección o la ruta no pudo resolverse de forma segura."""


def parse_geocode_response(data: dict, requested_address: str = "") -> dict:
    """Extrae coordenadas y rechaza resultados que no respetan el número."""
    status = data.get("status")
    results = data.get("results") or []

    if status == "ZERO_RESULTS" or not results:
        raise GeocodingError("dirección no encontrada")
    if status != "OK":
        raise GeocodingError(f"error de geocodificación: {status or 'desconocido'}")
    if len(results) != 1:
        raise GeocodingError("resultados ambiguos para la dirección")

    result = results[0]
    requested_number = re.search(r"\b(\d{1,5})\b", requested_address or "")
    returned_number = re.search(r"\b(\d{1,5})\b", result.get("formatted_address", ""))
    if requested_number and not returned_number:
        raise GeocodingError("el mapa no confirmó la altura solicitada")
    if requested_number and returned_number and requested_number.group(1) != returned_number.group(1):
        raise GeocodingError("el mapa devolvió otra altura para la dirección")
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
    """Convierte una dirección escrita en coordenadas de Bahía Blanca.

    Google sigue siendo la fuente principal. Nominatim es el fallback
    verificable cuando Google no encuentra una dirección local.
    """
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
        return parse_geocode_response(response.json(), address)
    except GeocodingError as exc:
        from app.integrations.nominatim import geocode_address as nominatim_geocode
        try:
            return nominatim_geocode(address)
        except Exception:
            raise exc
    except Exception as exc:
        raise GeocodingError(f"no se pudo geocodificar la dirección: {exc}") from exc


def geocode_suggestions(address: str) -> list[dict]:
    """Devuelve candidatos de Google aunque no confirme la altura."""
    if not GOOGLE_MAPS_API_KEY or not address.strip():
        return []
    import httpx
    query = f"{address}, Buenos Aires, Argentina"
    response = httpx.get(
        f"{GOOGLE_GEOCODING_URL}?address={quote_plus(query)}&language=es&region=ar&key={GOOGLE_MAPS_API_KEY}",
        timeout=15,
    )
    response.raise_for_status()
    output = []
    for result in (response.json().get("results") or [])[:5]:
        location = (result.get("geometry") or {}).get("location") or {}
        if "lat" not in location or "lng" not in location:
            continue
        output.append({
            "latitude": float(location["lat"]),
            "longitude": float(location["lng"]),
            "formatted_address": result.get("formatted_address", address.strip()),
            "placeId": result.get("place_id"),
        })
    return output


def route_distance_km(origin: dict, destination: dict) -> float:
    """Obtiene distancia vial; usa OSRM si Google rechaza la solicitud."""
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
        return _osrm_route_distance_km(origin, destination)
    except Exception as exc:
        raise GeocodingError(f"no se pudo calcular la distancia: {exc}") from exc


def route_geometry(origin: dict, destination: dict) -> dict:
    """Devuelve la geometría vial GeoJSON y sus métricas desde OSRM.

    Se llama únicamente con coordenadas ya verificadas por Livre Cloud; el
    navegador nunca contacta directamente al proveedor de mapas.
    """
    try:
        import httpx

        coordinates = (
            f"{origin['longitude']},{origin['latitude']};"
            f"{destination['longitude']},{destination['latitude']}"
        )
        response = httpx.get(
            f"{OSRM_ROUTE_URL}/{coordinates}",
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
            headers={"User-Agent": "LivreCloud/1.0"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        route = (data.get("routes") or [{}])[0]
        geometry = route.get("geometry") or {}
        coordinates_out = geometry.get("coordinates") or []
        if data.get("code") != "Ok" or len(coordinates_out) < 2:
            raise GeocodingError("la ruta no contiene una geometría válida")
        return {
            "coordinates": coordinates_out,
            "distance_km": float(route["distance"]) / 1000,
            "duration_min": float(route["duration"]) / 60,
        }
    except GeocodingError:
        raise
    except Exception as exc:
        raise GeocodingError(f"no se pudo obtener la geometría vial: {exc}") from exc


def _osrm_route_distance_km(origin: dict, destination: dict) -> float:
    """Fallback de distancia vial basado en las coordenadas ya verificadas."""
    try:
        import httpx

        coordinates = (
            f"{origin['longitude']},{origin['latitude']};"
            f"{destination['longitude']},{destination['latitude']}"
        )
        response = httpx.get(
            f"{OSRM_ROUTE_URL}/{coordinates}",
            params={"overview": "false"},
            headers={"User-Agent": "LivreCloud/1.0"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        route = (data.get("routes") or [{}])[0]
        meters = route.get("distance")
        if data.get("code") != "Ok" or not meters or meters <= 0:
            raise GeocodingError("no se pudo calcular la distancia vial")
        return meters / 1000
    except GeocodingError:
        raise
    except Exception as exc:
        raise GeocodingError(f"no se pudo calcular la distancia vial: {exc}") from exc
