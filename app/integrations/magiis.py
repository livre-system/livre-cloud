"""
app/integrations/magiis.py

Cliente HTTP para la API pública de MAGIIS.
Credenciales exclusivamente desde variables de entorno MAGIIS_USERNAME / MAGIIS_PASSWORD.
No expone tokens ni credenciales en logs.

Etapa actual: prueba controlada con generatedTravel=0 (cotización, no viaje real).
"""

import os
import logging
import httpx

log = logging.getLogger(__name__)

MAGIIS_BASE_URL  = "https://api.public.magiis.com"
MAGIIS_USERNAME  = os.getenv("MAGIIS_USERNAME", "")
MAGIIS_PASSWORD  = os.getenv("MAGIIS_PASSWORD", "")
MAGIIS_APP_NAME  = os.getenv("MAGIIS_APP_NAME", MAGIIS_USERNAME)  # por doc = username
MAGIIS_PASSENGER_USER_ID = int(os.getenv("MAGIIS_PASSENGER_USER_ID", "436133"))
MAGIIS_GENERATE_TRAVEL = int(os.getenv("MAGIIS_GENERATE_TRAVEL", "0"))

TIMEOUT = 15  # segundos


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────

def login() -> str | None:
    """
    Autentica contra MAGIIS y devuelve el token Bearer.
    Devuelve None si falla. No loguea credenciales ni token completo.
    """
    if not MAGIIS_USERNAME or not MAGIIS_PASSWORD:
        log.error("[MAGIIS] MAGIIS_USERNAME o MAGIIS_PASSWORD no configurados en Railway")
        return None

    try:
        resp = httpx.post(
            f"{MAGIIS_BASE_URL}/auth/login",
            headers={"RoleToAttempt": ""},
            json={"username": MAGIIS_USERNAME, "password": MAGIIS_PASSWORD},
            timeout=TIMEOUT,
        )
        log.info(f"[MAGIIS] login status={resp.status_code}")

        if resp.status_code != 200:
            log.error(f"[MAGIIS] login error: {resp.status_code} {resp.text[:300]}")
            return None

        token = resp.json().get("token", "")
        if not token:
            log.error("[MAGIIS] login: respuesta sin token")
            return None

        log.info("[MAGIIS] login OK — token obtenido")
        return token

    except Exception as e:
        log.error(f"[MAGIIS] login excepción: {e}", exc_info=True)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Trip create
# ─────────────────────────────────────────────────────────────────────────────

def trip_create(
    token: str,
    origin_lat: str,
    origin_lon: str,
    origin_name: str,
    destination_lat: str,
    destination_lon: str,
    destination_name: str,
    service_type: int = 1,
    passenger_user_id: int | None = None,
    guest_passenger_name: str | None = None,
    generated_travel: int = 0,
) -> dict:
    """
    Llama a POST /trip/create.

    generated_travel=0 → cotización (no crea viaje real)
    generated_travel=1 → crea viaje real

    Si passenger_user_id es None, no se envía el campo (prueba Guest).
    Si guest_passenger_name está presente, se envía como campo opcional.

    Devuelve dict con:
      - ok: bool
      - status_code: int
      - response: list | dict
      - error: str | None
    """
    if not MAGIIS_APP_NAME:
        return {"ok": False, "error": "MAGIIS_APP_NAME no configurado", "status_code": 0, "response": {}}

    payload = {
        "origin": {
            "latitude": origin_lat,
            "longitude": origin_lon,
            "shortName": origin_name,
        },
        "destination": {
            "latitude": destination_lat,
            "longitude": destination_lon,
            "shortName": destination_name,
        },
        "serviceType": service_type,
        "generatedTravel": generated_travel,
    }

    if passenger_user_id is not None:
        payload["passengerUserId"] = passenger_user_id

    if guest_passenger_name:
        payload["guestPassengerName"] = guest_passenger_name

    log.info(
        f"[MAGIIS] trip/create — "
        f"generatedTravel={generated_travel} "
        f"serviceType={service_type} "
        f"passenger_user_id={passenger_user_id!r} "
        f"guest={guest_passenger_name!r} "
        f"origin={origin_name!r} "
        f"destination={destination_name!r}"
    )

    try:
        resp = httpx.post(
            f"{MAGIIS_BASE_URL}/trip/create",
            headers={
                "Authorization": token,
                "ApplicationName": MAGIIS_APP_NAME,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=TIMEOUT,
        )

        log.info(f"[MAGIIS] trip/create status={resp.status_code}")
        log.info(f"[MAGIIS] trip/create response={resp.text[:500]}")

        try:
            response_data = resp.json()
        except Exception:
            response_data = {"raw": resp.text[:500]}

        return {
            "ok": resp.status_code in (200, 201),
            "status_code": resp.status_code,
            "response": response_data,
            "error": None if resp.status_code in (200, 201) else resp.text[:300],
        }

    except Exception as e:
        log.error(f"[MAGIIS] trip/create excepción: {e}", exc_info=True)
        return {"ok": False, "status_code": 0, "response": {}, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Flujo real del panel web
# ─────────────────────────────────────────────────────────────────────────────

MAGIIS_WEB_BASE_URL = "https://api.apps.magiis.com"


def simulate_all(
    token: str,
    carrier_user_id: int,
    passenger_user_id: int,
    operator_user_id: int,
    origin: dict,
    destination: dict,
    service_type_id: int,
    vehicle_type_id: int = 1,
    payment_method: str = "CASH",
    requester_user_id: int | None = None,
    pickup_gmt: str = "America/Argentina/Buenos_Aires",
    pax_qty: int = 1,
) -> dict:
    """Cotiza usando el mismo endpoint que el panel web de MAGIIS.

    Este endpoint solo simula la tarifa; no crea ni confirma un viaje.
    Requiere un Bearer de sesión web y los IDs operativos de MAGIIS.
    El token nunca se obtiene desde el usuario de la API pública.
    """
    if not token:
        return {"ok": False, "status_code": 0, "response": {}, "error": "MAGIIS_WEB_TOKEN no configurado"}

    payload = {
        "affiAgreementId": None,
        "destination": destination,
        "origin": origin,
        "firstName": None,
        "lastName": None,
        "mail": None,
        "note": ", ",
        "passengerUserId": passenger_user_id,
        "phoneNumber": None,
        "platform": "Web",
        "travelPreferences": {
            "airConditioner": False,
            "luggage": False,
            "mascots": False,
            "vehicleTypeId": vehicle_type_id,
            "driverUserId": None,
        },
        "paymentMethod": payment_method,
        "carrierUserId": carrier_user_id,
        "isDelivery": False,
        "isRental": False,
        "rentalType": None,
        "rentalHours": "1",
        "requesterUserId": requester_user_id if requester_user_id is not None else passenger_user_id,
        "isProgrammed": False,
        "dateProgrammed": None,
        "driverProgrammedId": 0,
        "waypoints": None,
        "roundTrip": False,
        "avoidTolls": False,
        "avoidHighways": False,
        "fastestRoute": False,
        "contractorEmployeeId": None,
        "operatorUserId": operator_user_id,
        "pbxCaller": False,
        "pickupGmt": pickup_gmt,
        "qrPayment": False,
        "serviceTypeId": str(service_type_id),
        "areaId": None,
        "paxQty": pax_qty,
    }

    log.info(
        "[MAGIIS] travels/simulateAll carrier=%s passenger=%s service_type=%s vehicle_type=%s",
        carrier_user_id,
        passenger_user_id,
        service_type_id,
        vehicle_type_id,
    )

    try:
        resp = httpx.post(
            f"{MAGIIS_WEB_BASE_URL}/carriers/{carrier_user_id}/travels/simulateAll",
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=TIMEOUT,
        )
        try:
            response_data = resp.json()
        except Exception:
            response_data = {"raw": resp.text[:1000]}
        return {
            "ok": resp.status_code in (200, 201),
            "status_code": resp.status_code,
            "response": response_data,
            "error": None if resp.status_code in (200, 201) else resp.text[:500],
        }
    except Exception as e:
        log.error("[MAGIIS] travels/simulateAll excepción: %s", e, exc_info=True)
        return {"ok": False, "status_code": 0, "response": {}, "error": str(e)}
