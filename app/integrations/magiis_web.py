"""Cliente separado para el servicio Web de MAGIIS.

No comparte credenciales ni contrato con app.integrations.magiis (API pública).
Los tokens solo viven en memoria del proceso que ejecuta estas funciones.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

BASE_URL = "https://api.apps.magiis.com"
CARRIER_USER_ID = 2421
PASSENGER_USER_ID = 436133
OPERATOR_USER_ID = 323327
SERVICE_TYPE_ID = "558"
TIMEOUT = 30.0


def _credentials() -> tuple[str, str]:
    username = os.getenv("MAGIIS_WEB_USERNAME", "")
    password = os.getenv("MAGIIS_WEB_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("MAGIIS_WEB_USERNAME/MAGIIS_WEB_PASSWORD no configuradas")
    return username, password


def login(client: httpx.Client | None = None) -> dict[str, Any]:
    """Inicia sesión como carrier Web y devuelve token/userId en memoria."""
    username, password = _credentials()
    own_client = client is None
    client = client or httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)
    try:
        response = client.post(
            "/auth/login",
            headers={"RoleToAttempt": "ROLE_CARRIER"},
            json={"username": username, "password": password},
        )
        if response.status_code != 200:
            raise RuntimeError(f"login Web HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        token = data.get("token")
        if not token:
            raise RuntimeError("login Web sin token")
        return {"token": token, "userId": data.get("userId"), "response": data}
    finally:
        if own_client:
            client.close()


def _request_with_reauth(
    method: str,
    path: str,
    *,
    session: dict[str, Any],
    client: httpx.Client,
    **kwargs: Any,
) -> tuple[httpx.Response, dict[str, Any]]:
    """Hace una request y, ante 401/403, reloguea y reintenta una vez."""
    for attempt in range(2):
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {session['token']}"
        response = client.request(method, path, headers=headers, **kwargs)
        if response.status_code not in (401, 403) or attempt == 1:
            return response, session
        session = login(client)
    raise AssertionError("unreachable")


def service_types(
    *,
    session: dict[str, Any],
    client: httpx.Client | None = None,
    service_type: str = "REGULAR",
) -> tuple[httpx.Response, dict[str, Any]]:
    own_client = client is None
    client = client or httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)
    try:
        return _request_with_reauth(
            "GET",
            f"/carriers/{CARRIER_USER_ID}/serviceTypes",
            session=session,
            client=client,
            params={"serviceType": service_type},
            headers={"Accept": "application/json"},
        )
    finally:
        if own_client:
            client.close()


def _creation_location(location: dict[str, Any], *, is_origin: bool = False) -> dict[str, Any]:
    payload = {
        "longitude": location.get("longitude"),
        "latitude": location.get("latitude"),
        "placeId": location.get("placeId"),
        "shortName": location.get("shortName"),
        "additionalInfo": location.get("additionalInfo"),
    }
    if is_origin:
        payload["specialInstruction"] = location.get("specialInstruction", "")
    payload["floorApt"] = location.get("floorApt", "")
    payload["waitTime"] = location.get("waitTime")
    return payload


def _fix_concept_list() -> list[dict[str, Any]]:
    concepts = (
        (526, 1, "FARE"),
        (527, 2, "WAIT"),
        (528, 3, "PARKING"),
        (529, 4, "TOLL"),
        (530, 5, "OTHER_COST"),
        (531, 6, "TIPS"),
    )
    return [
        {
            "id": concept_id,
            "conceptCode": concept_code,
            "name": name,
            "fixValue": None,
            "fixAmount": None,
            "conceptVersion": 1,
            "taxesAmount": 0,
            "deleted": False,
            "taxesList": [],
        }
        for concept_id, concept_code, name in concepts
    ]


def create_travel(
    *,
    session: dict[str, Any],
    origin: dict[str, Any],
    destination: dict[str, Any],
    vehicle_type_id: int = 2,
    passenger_user_id: int = PASSENGER_USER_ID,
    operator_user_id: int = OPERATOR_USER_ID,
    service_type_id: str = SERVICE_TYPE_ID,
    origin_token: str = "INVALID_TOKEN",
    is_programmed: bool = False,
    date_programmed: str | None = None,
    client: httpx.Client | None = None,
) -> tuple[httpx.Response, dict[str, Any], dict[str, Any]]:
    """Prepara POST /travels; no se invoca desde las pruebas de cotización."""
    payload = {
        "affiAgreementId": None,
        "carrierUserId": CARRIER_USER_ID,
        "destination": _creation_location(destination),
        "firstName": None,
        "lastName": None,
        "mail": None,
        "note": "",
        "origin": _creation_location(origin, is_origin=True),
        "passengerUserId": passenger_user_id,
        "paymentMethod": "CASH",
        "phoneNumber": None,
        "platform": "Web",
        "travelPreferences": {
            "airConditioner": False,
            "luggage": False,
            "mascots": False,
            "vehicleTypeId": vehicle_type_id,
            "driverUserId": None,
        },
        "originToken": origin_token,
        "isDelivery": False,
        "isRental": False,
        "rentalHours": "1",
        "requesterUserId": passenger_user_id,
        "isProgrammed": is_programmed,
        "dateProgrammed": date_programmed,
        "driverProgrammedId": 0,
        "recurringValue": None,
        "recurringEnd": None,
        "hasGuestPassenger": False,
        "guestPassengerName": "",
        "waypoints": None,
        "avoidHighways": False,
        "avoidTolls": False,
        "fastestRoute": False,
        "recurringPattern": None,
        "selectedDaysOfWeek": None,
        "cardDetail": None,
        "airlineName": None,
        "departArrival": None,
        "flightNumber": None,
        "departReturnSchedule": None,
        "operatorUserId": operator_user_id,
        "pbxCaller": False,
        "tipPercentage": None,
        "tipAmount": None,
        "driverInstructions": "",
        "fixedCost": None,
        "multiTripSeq": None,
        "pickupGmt": "America/Argentina/Buenos_Aires",
        "otherCosts": None,
        "fixConceptList": _fix_concept_list(),
        "calculateTaxes": False,
        "fixConceptsCost": 0,
        "taxesCost": 0,
        "qrPayment": False,
        "serviceTypeId": service_type_id,
        "areaId": None,
        "paxQty": 1,
        "createdBy": "CARRIER",
        "isSpecialAssign": False,
    }
    own_client = client is None
    client = client or httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)
    try:
        response, refreshed = _request_with_reauth(
            "POST",
            f"/carriers/{CARRIER_USER_ID}/travels",
            session=session,
            client=client,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
        )
        return response, refreshed, payload
    finally:
        if own_client:
            client.close()


def audit_travel(
    *,
    session: dict[str, Any],
    travel_id: int,
    channel_user_id: int = OPERATOR_USER_ID,
    client: httpx.Client | None = None,
) -> tuple[httpx.Response, dict[str, Any], dict[str, Any]]:
    """Prepara PUT /audit para un travel_id devuelto por MAGIIS."""
    payload = {
        "travelId": travel_id,
        "carrierUserId": str(CARRIER_USER_ID),
        "channelUserId": channel_user_id,
    }
    own_client = client is None
    client = client or httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)
    try:
        response, refreshed = _request_with_reauth(
            "PUT",
            f"/carriers/{CARRIER_USER_ID}/travels/{travel_id}/audit",
            session=session,
            client=client,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
        )
        return response, refreshed, payload
    finally:
        if own_client:
            client.close()


def cancel_travel(
    *,
    session: dict[str, Any],
    travel_id: int,
    operator_name: str,
    reason: str = "Cancelación solicitada por el pasajero",
    user_id: int = OPERATOR_USER_ID,
    client: httpx.Client | None = None,
) -> tuple[httpx.Response, dict[str, Any], dict[str, Any]]:
    """Prepara PUT /cancel para un travel_id devuelto por MAGIIS."""
    payload = {
        "travelId": travel_id,
        "carrierUserId": str(CARRIER_USER_ID),
        "reasonForCancellation": reason,
        "canceledBy": "CARRIER",
        "name": operator_name,
        "userId": user_id,
        "checkPassengerCancelation": False,
    }
    own_client = client is None
    client = client or httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)
    try:
        response, refreshed = _request_with_reauth(
            "PUT",
            f"/carriers/{CARRIER_USER_ID}/travels/{travel_id}/cancel",
            session=session,
            client=client,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
        )
        return response, refreshed, payload
    finally:
        if own_client:
            client.close()


def resolve_address(
    address: str,
    *,
    city: str = "Bahía Blanca",
) -> dict[str, Any]:
    """Obtiene coordenadas sin Google; MAGIIS asigna luego el placeId."""
    if not address or not address.strip():
        raise ValueError("dirección vacía")
    from app.integrations.nominatim import geocode_address

    resolved = geocode_address(address.strip(), city=city)
    return {
        "latitude": str(resolved["latitude"]),
        "longitude": str(resolved["longitude"]),
        "placeId": None,
        "shortName": resolved.get("shortName") or address.strip(),
    }


def get_place(
    *,
    session: dict[str, Any],
    place_id: str | None = None,
    latitude: str | float | None = None,
    longitude: str | float | None = None,
    client: httpx.Client | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Obtiene el lugar desde MAGIIS por placeId o coordenadas."""
    if not place_id and (latitude is None or longitude is None):
        raise ValueError("se requiere placeId o coordenadas")
    own_client = client is None
    client = client or httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)
    assert client is not None
    try:
        response, refreshed = _request_with_reauth(
            "GET",
            "/places/getPlace",
            session=session,
            client=client,
            params=(
                {"placeId": place_id}
                if place_id
                else {"latitude": latitude, "longitude": longitude}
            ),
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"MAGIIS Web getPlace HTTP {response.status_code}: {response.text[:300]}"
            )
        body = response.json()
        if not isinstance(body, list) or not body or not isinstance(body[0], dict):
            raise RuntimeError("MAGIIS Web getPlace devolvió una respuesta inválida")
        item = body[0]
        location = (item.get("geometry") or {}).get("location") or {}
        if "lat" not in location or "lng" not in location:
            raise RuntimeError("MAGIIS Web getPlace no devolvió coordenadas")
        return {
            "latitude": str(location["lat"]),
            "longitude": str(location["lng"]),
            "placeId": item.get("place_id") or place_id,
            "shortName": "",
        }, refreshed
    finally:
        if own_client:
            client.close()


def _location(
    latitude: str,
    longitude: str,
    short_name: str,
    *,
    place_id: str | None = None,
    floor_apt: str | None = None,
) -> dict[str, Any]:
    return {
        "longitude": longitude,
        "latitude": latitude,
        "placeId": place_id,
        "shortName": short_name,
        "additionalInfo": None,
        "floorApt": floor_apt,
        "waitTime": None,
    }


def simulate_all(
    *,
    session: dict[str, Any],
    origin_lat: str,
    origin_lon: str,
    origin_name: str,
    destination_lat: str,
    destination_lon: str,
    destination_name: str,
    origin_place_id: str | None = None,
    destination_place_id: str | None = None,
    passenger_user_id: int = PASSENGER_USER_ID,
    operator_user_id: int = OPERATOR_USER_ID,
    service_type_id: str = SERVICE_TYPE_ID,
    vehicle_type_id: int = 2,
    client: httpx.Client | None = None,
    livi_trip_id: int | None = None,
) -> tuple[httpx.Response, dict[str, Any], dict[str, Any]]:
    """Reproduce la estructura del simulateAll capturado en el HAR."""
    payload = {
        "affiAgreementId": None,
        "destination": _location(
            destination_lat,
            destination_lon,
            destination_name,
            place_id=destination_place_id,
        ),
        "origin": _location(
            origin_lat,
            origin_lon,
            origin_name,
            place_id=origin_place_id,
        ),
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
        "paymentMethod": "CASH",
        "carrierUserId": CARRIER_USER_ID,
        "isDelivery": False,
        "isRental": False,
        "rentalType": None,
        "rentalHours": "1",
        "requesterUserId": passenger_user_id,
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
        "pickupGmt": "America/Argentina/Buenos_Aires",
        "qrPayment": False,
        "serviceTypeId": service_type_id,
        "areaId": None,
        "paxQty": 1,
    }
    own_client = client is None
    client = client or httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)
    try:
        response, refreshed = _request_with_reauth(
            "POST",
            f"/carriers/{CARRIER_USER_ID}/travels/simulateAll",
            session=session,
            client=client,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
        )
        if livi_trip_id is not None:
            from app.integrations.magiis_quotes import persist_quote_response

            try:
                response_body: Any = response.json()
            except ValueError:
                response_body = {"raw": response.text}
            persist_quote_response(
                livi_trip_id=livi_trip_id,
                http_status=response.status_code,
                request_payload=payload,
                response_body=response_body,
            )
        return response, refreshed, payload
    finally:
        if own_client:
            client.close()
