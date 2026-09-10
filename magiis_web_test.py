"""Prueba segura del flujo web de MAGIIS (solo cotización)."""

import json
import os
import sys

from app.integrations.magiis import simulate_all
from app.integrations.nominatim import GeocodingError, geocode_address

ORIGIN = "Chiclana 5"
DESTINATION = "Terrada 28"


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Falta la variable {name}")
    return value


def main() -> int:
    token = required("MAGIIS_WEB_TOKEN")
    carrier_id = int(required("MAGIIS_CARRIER_ID"))
    passenger_id = int(required("MAGIIS_PASSENGER_USER_ID"))
    operator_id = int(required("MAGIIS_OPERATOR_USER_ID"))
    service_type_id = int(required("MAGIIS_SERVICE_TYPE_ID"))
    vehicle_type_id = int(os.getenv("MAGIIS_VEHICLE_TYPE_ID", "1"))

    print("[1] Geocodificando con Nominatim...")
    try:
        origin = geocode_address(ORIGIN)
        destination = geocode_address(DESTINATION)
    except GeocodingError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(json.dumps({"origin": origin, "destination": destination}, ensure_ascii=False))

    print("[2] Cotizando con travels/simulateAll (no crea viaje)...")
    result = simulate_all(
        token=token,
        carrier_user_id=carrier_id,
        passenger_user_id=passenger_id,
        operator_user_id=operator_id,
        origin=origin,
        destination=destination,
        service_type_id=service_type_id,
        vehicle_type_id=vehicle_type_id,
    )
    print(f"status_code={result['status_code']} ok={result['ok']}")
    print(json.dumps(result["response"], ensure_ascii=False))
    if result["error"]:
        print(f"ERROR: {result['error']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
