"""
magiis_test.py — Prueba controlada de MAGIIS (cotización, no viaje real)

Ejecutar desde la raíz del proyecto en Railway console:
  python magiis_test.py

Qué hace:
  1. Login con MAGIIS_USERNAME / MAGIIS_PASSWORD
  2. Llama trip/create con generatedTravel=0 (cotización)
  3. NO envía passengerUserId (prueba Guest)
  4. Loguea status y respuesta completa
  5. Documenta si MAGIIS acepta o rechaza sin passengerUserId

No crea viajes reales. No modifica ningún sistema existente.
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

# Coordenadas reales de Bahía Blanca para la prueba
# España 24, Bahía Blanca
TEST_ORIGIN_LAT  = "-38.7196"
TEST_ORIGIN_LON  = "-62.2724"
TEST_ORIGIN_NAME = "España 24, Bahía Blanca, Buenos Aires, Argentina"

# Uruguay 100, Bahía Blanca
TEST_DEST_LAT    = "-38.7183"
TEST_DEST_LON    = "-62.2698"
TEST_DEST_NAME   = "Uruguay 100, Bahía Blanca, Buenos Aires, Argentina"

TEST_GUEST_NAME  = "Pasajero LIVI Test"
TEST_SERVICE_TYPE = 1  # Standard/Regular — ajustar si MAGIIS devuelve error de tipo


def check_env():
    missing = []
    if not os.getenv("MAGIIS_USERNAME"):
        missing.append("MAGIIS_USERNAME")
    if not os.getenv("MAGIIS_PASSWORD"):
        missing.append("MAGIIS_PASSWORD")
    if missing:
        print(f"\n❌ Variables de entorno faltantes en Railway: {', '.join(missing)}")
        sys.exit(1)
    print(f"✅ MAGIIS_USERNAME={os.getenv('MAGIIS_USERNAME')}")
    print(f"✅ MAGIIS_APP_NAME={os.getenv('MAGIIS_APP_NAME', os.getenv('MAGIIS_USERNAME'))}")


def main():
    print("\n" + "=" * 60)
    print("  MAGIIS — Prueba controlada (generatedTravel=0)")
    print("  NO se crea ningún viaje real")
    print("=" * 60)

    check_env()

    from app.integrations.magiis import login, trip_create

    # ── Paso 1: Login ────────────────────────────────────────────────────────
    print("\n[1] Autenticando...")
    token = login()
    if not token:
        print("❌ Login fallido — revisá MAGIIS_USERNAME y MAGIIS_PASSWORD en Railway")
        sys.exit(1)
    print("✅ Login OK")

    # ── Paso 2: Cotización sin passengerUserId (prueba Guest) ────────────────
    print("\n[2] Llamando trip/create (generatedTravel=0, passengerUserId=436133)...")
    result = trip_create(
        token=token,
        origin_lat=TEST_ORIGIN_LAT,
        origin_lon=TEST_ORIGIN_LON,
        origin_name=TEST_ORIGIN_NAME,
        destination_lat=TEST_DEST_LAT,
        destination_lon=TEST_DEST_LON,
        destination_name=TEST_DEST_NAME,
        service_type=TEST_SERVICE_TYPE,
        passenger_user_id=436133,        # ← pasajero técnico Livre Cloud
        guest_passenger_name=None,
        generated_travel=0,              # ← solo cotización, NO crea viaje real
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
        print(f"\n✅ MAGIIS aceptó el request sin passengerUserId")
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
