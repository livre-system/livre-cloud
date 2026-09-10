"""
magiis_test.py — Prueba controlada de MAGIIS (cotización, no viaje real)

Ejecutar desde la raíz del proyecto en Railway console:
  python magiis_test.py

Qué hace:
  1. Login con MAGIIS_USERNAME / MAGIIS_PASSWORD
  2. Geocodifica las direcciones con GOOGLE_MAPS_API_KEY
  3. Llama trip/create con generatedTravel=0 (cotización)
  4. Usa el pasajero técnico configurado
  5. No crea viajes reales
"""

import os
import sys
import json
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Direcciones de Bahía Blanca para la prueba
TEST_ORIGIN_NAME = "Mitre 300, Bahía Blanca"
TEST_DEST_NAME   = "Moreno 13, Bahía Blanca"
TEST_SERVICE_TYPE = 1  # Standard/Regular — ajustar si MAGIIS devuelve error de tipo


def check_env():
    missing = []
    for name in ("MAGIIS_USERNAME", "MAGIIS_PASSWORD", "GOOGLE_MAPS_API_KEY"):
        if not os.getenv(name):
            missing.append(name)
    if missing:
        print(f"\n❌ Variables de entorno faltantes en Railway: {', '.join(missing)}")
        sys.exit(1)
    print("✅ Variables de MAGIIS y Google Maps configuradas")


def main():
    print("\n" + "=" * 60)
    print("  MAGIIS — Prueba controlada (generatedTravel=0)")
    print("  NO se crea ningún viaje real")
    print("=" * 60)

    check_env()

    from app.integrations.google_maps import geocode_address, GeocodingError
    from app.integrations.magiis import (
        MAGIIS_PASSENGER_USER_ID,
        login,
        trip_create,
    )

    # ── Paso 1: Login ────────────────────────────────────────────────────────
    print("\n[1] Autenticando...")
    token = login()
    if not token:
        print("❌ Login fallido — revisá MAGIIS_USERNAME y MAGIIS_PASSWORD en Railway")
        sys.exit(1)
    print("✅ Login OK")

    # ── Paso 2: Geocodificación ───────────────────────────────────────────────
    print("\n[2] Geocodificando origen y destino...")
    try:
        origin = geocode_address(TEST_ORIGIN_NAME)
        destination = geocode_address(TEST_DEST_NAME)
    except GeocodingError as exc:
        print(f"❌ No se pudo geocodificar una dirección: {exc}")
        sys.exit(1)
    print(f"✅ Origen: {origin['formatted_address']}")
    print(f"✅ Destino: {destination['formatted_address']}")

    # ── Paso 3: Cotización sin crear viaje real ───────────────────────────────
    print("\n[3] Llamando trip/create (generatedTravel=0)...")
    result = trip_create(
        token=token,
        origin_lat=str(origin["latitude"]),
        origin_lon=str(origin["longitude"]),
        origin_name=origin["formatted_address"],
        destination_lat=str(destination["latitude"]),
        destination_lon=str(destination["longitude"]),
        destination_name=destination["formatted_address"],
        service_type=TEST_SERVICE_TYPE,
        passenger_user_id=MAGIIS_PASSENGER_USER_ID,
        guest_passenger_name=None,
        generated_travel=0,
    )

    print(f"\n  status_code : {result['status_code']}")
    print(f"  ok          : {result['ok']}")

    print(f"\n  Respuesta completa de MAGIIS:")
    import json
    print(json.dumps(result["response"], indent=2, ensure_ascii=False))

    if result["error"]:
        print(f"\n❌ MAGIIS rechazó — status={result['status_code']}")
        print(f"   {result['error']}")
    else:
        print("\n✅ MAGIIS aceptó el request")
        print(f"\n  Respuesta (cotizaciones):")
        print(json.dumps(result["response"], indent=2, ensure_ascii=False))

        if isinstance(result["response"], list):
            for q in result["response"]:
                name  = q.get("serviceName", "?")
                price = q.get("pricePointToPoint", "?")
                dist  = q.get("distance", "?")
                dur   = q.get("duration", "?")
                print(f"\n  Servicio: {name} | Precio: ${price} | Distancia: {dist}km | Duración: {dur}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
