"""
app/voice/elevenlabs_trip.py

Endpoint POST /elevenlabs/trip
Recibe desde ElevenLabs el origen y destino confirmados durante una llamada
y registra el pedido de viaje en livi_trips.

Payload esperado (Webhook Tool de ElevenLabs):
{
  "tool_call_id": "...",
  "tool_name": "registrar_viaje",
  "parameters": {
    "origen": "España 24",
    "destino": "Uruguay 100",
    "conversation_id": "conv_..."   ← opcional, también viene en raíz
  },
  "conversation_id": "conv_..."
}

Idempotente: un conversation_id = un viaje. Si ElevenLabs reintenta, devuelve OK.
Conecta el pedido con MAGIIS Web cuando `MAGIIS_WEB_FLOW_ENABLED=1`.
La creación real requiere además `MAGIIS_WEB_CREATE_ENABLED=1`.
"""

import logging
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from fastapi import APIRouter, Request, HTTPException
from app.core.database import get_cursor
from app.integrations import magiis_web
from app.integrations.google_maps import geocode_address, route_distance_km
from app.integrations.historical_quote import historical_options

log = logging.getLogger(__name__)

router = APIRouter(prefix="/elevenlabs", tags=["elevenlabs"])

TRACKING_BASE_URL = os.getenv(
    "PASSENGER_TRACKING_BASE_URL",
    "https://livre-system.github.io/livre-pasajeros/",
).rstrip("/")


def _tracking_url(token: str | None) -> str | None:
    return f"{TRACKING_BASE_URL}/?token={token}" if token else None


def _get_existing_trip(conversation_id: str) -> dict | None:
    """Devuelve el viaje si ya existe para este conversation_id."""
    try:
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM livi_trips WHERE conversation_id = %s LIMIT 1;",
                [conversation_id],
            )
            return cur.fetchone()
    except Exception as e:
        log.error(f"[ELEVEN TRIP] ERROR verificando idempotencia: {e}")
        return None


def _save_trip(
    conversation_id: str,
    origen: str,
    destino: str,
    whatsapp_user: str = "",
    tool_call_id: str = "",
) -> tuple[int, str]:
    """Inserta el viaje y devuelve el id y token privado de seguimiento."""
    tracking_token = secrets.token_urlsafe(32)
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO livi_trips
                (conversation_id, origen, destino, whatsapp_user, tool_call_id, tracking_token, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s)
            RETURNING id;
            """,
            (
                conversation_id,
                origen,
                destino,
                whatsapp_user or "",
                tool_call_id or "",
                tracking_token,
                datetime.now(timezone.utc),
                datetime.now(timezone.utc),
            ),
        )
        return cur.fetchone()["id"], tracking_token


def _get_latest_active_trip(whatsapp_user: str) -> dict | None:
    """Busca el último viaje MAGIIS activo del pasajero de WhatsApp."""
    if not whatsapp_user:
        return None
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT * FROM livi_trips
            WHERE whatsapp_user = %s
              AND magiis_travel_id IS NOT NULL
              AND status IN ('created', 'pending')
              AND COALESCE(magiis_status, '') NOT IN ('cancelled', 'canceled')
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            [whatsapp_user],
        )
        return cur.fetchone()


def _update_magiis_state(
    trip_id: int,
    *,
    status: str,
    travel_id: int | None = None,
    response: str | None = None,
    error: str | None = None,
) -> None:
    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE livi_trips
            SET status = %s,
                magiis_travel_id = %s,
                magiis_status = %s,
                magiis_response = %s,
                magiis_error = %s,
                magiis_updated_at = %s,
                updated_at = %s
            WHERE id = %s;
            """,
            (
                status,
                travel_id,
                status,
                response,
                error,
                datetime.now(timezone.utc),
                datetime.now(timezone.utc),
                trip_id,
            ),
        )


def _find_travel_id(value: object) -> int | None:
    if isinstance(value, dict):
        for key in ("travelId", "id"):
            candidate = value.get(key)
            if isinstance(candidate, int):
                return candidate
        for child in value.values():
            found = _find_travel_id(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_travel_id(child)
            if found is not None:
                return found
    return None


def _run_magiis_web(
    trip_id: int,
    origen: str,
    destino: str,
    date_programmed: str | None = None,
    service_type_id: str | None = None,
) -> dict:
    """Resuelve texto en la capa Web y cotiza/crea el viaje."""
    try:
        with httpx.Client(
            base_url=magiis_web.BASE_URL, timeout=magiis_web.TIMEOUT
        ) as client:
            session = magiis_web.login(client)
            origin_text = magiis_web.resolve_address(origen)
            destination_text = magiis_web.resolve_address(destino)
            origin, session = magiis_web.get_place(
                session=session,
                place_id=origin_text.get("placeId"),
                latitude=origin_text["latitude"],
                longitude=origin_text["longitude"],
                client=client,
            )
            destination, session = magiis_web.get_place(
                session=session,
                place_id=destination_text.get("placeId"),
                latitude=destination_text["latitude"],
                longitude=destination_text["longitude"],
                client=client,
            )
            simulation, session, _ = magiis_web.simulate_all(
                session=session,
                origin_lat=origin["latitude"],
                origin_lon=origin["longitude"],
                origin_name=origin["shortName"] or origen,
                destination_lat=destination["latitude"],
                destination_lon=destination["longitude"],
                destination_name=destination["shortName"] or destino,
                origin_place_id=origin["placeId"],
                destination_place_id=destination["placeId"],
                client=client,
                livi_trip_id=trip_id,
            )
            body = simulation.json()
            options = body if isinstance(body, list) else []
            selected = next(
                (item for item in options
                 if str(item.get("transportTypeId")) == str(service_type_id or "2")
                 and item.get("serviceStatus") == "AVAILABLE"),
                None,
            )
            if selected is None:
                raise RuntimeError("MAGIIS Web no ofreció el servicio seleccionado disponible")

            if os.getenv("MAGIIS_WEB_CREATE_ENABLED", "0") != "1":
                _update_magiis_state(
                    trip_id, status="quoted", response=str(body)[:10000]
                )
                return {"created": False, "quote": selected}

            created, _, _ = magiis_web.create_travel(
                session=session,
                origin={**origin, "shortName": origin["shortName"] or origen},
                destination={**destination, "shortName": destination["shortName"] or destino},
                client=client,
                is_programmed=bool(date_programmed),
                date_programmed=date_programmed,
                service_type_id=str(service_type_id or "2"),
            )
            created_body = created.json()
            travel_id = _find_travel_id(created_body)
            if created.status_code not in (200, 201) or travel_id is None:
                raise RuntimeError(
                    f"MAGIIS Web creación HTTP {created.status_code} sin travelId"
                )
            _update_magiis_state(
                trip_id,
                status="created",
                travel_id=travel_id,
                response=str(created_body)[:10000],
            )
            return {"created": True, "travel_id": travel_id}
    except Exception as exc:
        log.error("[ELEVEN TRIP] MAGIIS Web error: %s", exc, exc_info=True)
        _update_magiis_state(trip_id, status="magiis_error", error=str(exc)[:500])
        return {"created": False, "error": str(exc)}


def _run_local_quote(origen: str, destino: str) -> dict:
    """Cotiza con parámetros históricos; no consulta MAGIIS."""
    try:
        origin = geocode_address(origen)
        destination = geocode_address(destino)
        distance_km = route_distance_km(origin, destination)
        options = historical_options(Decimal(str(distance_km)))
        selected = next(
            (item for item in options if item["transportTypeId"] == "2"),
            options[0],
        )
        return {
            "price": selected["price"],
            "options": options,
            "locations": {"origin": origin, "destination": destination},
            "distance_km": distance_km,
            "source": "historical_quotes",
        }
    except Exception as exc:
        log.error("[LOCAL QUOTE] error: %s", exc, exc_info=True)
        return {"error": str(exc)}


@router.post("/quote")
async def elevenlabs_quote(request: Request):
    """Cotiza un viaje sin crear el viaje real en MAGIIS."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc

    parameters = body.get("parameters", {}) or {}
    data = body.get("data", {}) or {}
    origen = (
        parameters.get("origen") or body.get("origen") or data.get("origen") or ""
    ).strip()
    destino = (
        parameters.get("destino") or body.get("destino") or data.get("destino") or ""
    ).strip()
    whatsapp_user = (
        parameters.get("whatsapp_user")
        or body.get("whatsapp_user")
        or data.get("whatsapp_user")
        or ""
    ).strip()

    log.info(
        "[ELEVEN QUOTE] origen=%r destino=%r whatsapp=%r",
        origen, destino, whatsapp_user,
    )
    if not origen:
        return {"result": "Error: el origen no puede estar vacío."}
    if not destino:
        return {"result": "Error: el destino no puede estar vacío."}

    quote = _run_local_quote(origen, destino)
    if quote.get("error"):
        return {"result": "No pude cotizar el viaje."}
    return {
        "result": f"El viaje cuesta ${quote['price']}.",
        "options": quote.get("options", []),
        "locations": quote.get("locations", {}),
    }


@router.post("/status")
async def elevenlabs_trip_status(request: Request):
    """Devuelve al agente el estado y la asignación del último viaje activo."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc

    parameters = body.get("parameters", {}) or {}
    data = body.get("data", {}) or {}
    whatsapp_user = (
        parameters.get("whatsapp_user")
        or body.get("whatsapp_user")
        or body.get("user_id")
        or data.get("whatsapp_user")
        or data.get("user_id")
        or ""
    ).strip()
    if not whatsapp_user:
        return {"result": "No pude identificar al pasajero de WhatsApp."}

    trip = _get_latest_active_trip(whatsapp_user)
    if not trip:
        return {"result": "No encontré un viaje activo para este pasajero."}

    driver = trip.get("tracking_driver_name") or trip.get("magiis_driver_first_name")
    vehicle_parts = [
        trip.get("tracking_vehicle_model") or trip.get("magiis_vehicle_model"),
        trip.get("tracking_vehicle_color") or trip.get("magiis_vehicle_color"),
    ]
    vehicle = " ".join(part for part in vehicle_parts if part).strip()
    plate = trip.get("tracking_plate") or trip.get("magiis_car_plate") or trip.get("magiis_vehicle_domain")

    if not driver and not vehicle and not plate:
        return {
            "result": "El viaje está registrado y todavía estamos buscando un conductor.",
            "status": trip.get("magiis_status") or trip.get("status"),
        }

    assignment = []
    if driver:
        assignment.append(f"conductor {driver}")
    if vehicle:
        assignment.append(f"vehículo {vehicle}")
    if plate:
        assignment.append(f"patente {plate}")
    return {
        "result": "Ya hay una asignación: " + ", ".join(assignment) + ".",
        "status": trip.get("magiis_status") or trip.get("status"),
        "driver": driver or None,
        "vehicle": vehicle or None,
        "plate": plate or None,
    }


@router.get("/tracking/{tracking_token}")
def passenger_tracking(tracking_token: str):
    """Devuelve sólo los datos públicos mínimos del viaje para el mapa."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, origen, destino, status, magiis_status,
                   tracking_driver_name, tracking_plate,
                   tracking_vehicle_color, tracking_vehicle_model,
                   tracking_latitude, tracking_longitude,
                   tracking_location_updated_at, tracking_location_visible_at,
                   magiis_driver_first_name, magiis_vehicle_model,
                   magiis_vehicle_color, magiis_car_plate, magiis_vehicle_domain,
                   magiis_updated_at
            FROM livi_trips
            WHERE tracking_token = %s
            LIMIT 1;
            """,
            [tracking_token],
        )
        trip = cur.fetchone()
    if not trip:
        raise HTTPException(status_code=404, detail="Seguimiento no encontrado")

    driver = trip.get("tracking_driver_name") or trip.get("magiis_driver_first_name") or None
    vehicle_parts = [
        trip.get("tracking_vehicle_model") or trip.get("magiis_vehicle_model"),
        trip.get("tracking_vehicle_color") or trip.get("magiis_vehicle_color"),
    ]
    vehicle = " ".join(part for part in vehicle_parts if part).strip() or None
    plate = trip.get("tracking_plate") or trip.get("magiis_car_plate") or trip.get("magiis_vehicle_domain")
    location = None
    if trip.get("tracking_latitude") is not None and trip.get("tracking_longitude") is not None:
        location = {
            "latitude": trip["tracking_latitude"],
            "longitude": trip["tracking_longitude"],
        }
    location_updated_at = trip.get("tracking_location_updated_at")
    visible_at = trip.get("tracking_location_visible_at")
    if visible_at and visible_at.tzinfo is None:
        visible_at = visible_at.replace(tzinfo=timezone.utc)
    if visible_at and visible_at > datetime.now(timezone.utc):
        location = None
        location_updated_at = None
    return {
        "trip_id": trip["id"],
        "origin": trip["origen"],
        "destination": trip["destino"],
        "status": trip.get("magiis_status") or trip.get("status"),
        "driver": driver,
        "vehicle": vehicle,
        "plate": plate,
        "location": location,
        "location_updated_at": location_updated_at,
        "source": "crm",
    }


@router.post("/trip")
async def elevenlabs_trip(request: Request):
    """
    Recibe el Webhook Tool 'registrar_viaje' de ElevenLabs.
    Registra el pedido de viaje en livi_trips.
    Responde { "result": "..." } que ElevenLabs usa como confirmación al agente.
    """
    # ── 1. Parsear body ──────────────────────────────────────────────────────
    try:
        body = await request.json()
    except Exception as e:
        log.error(f"[ELEVEN TRIP] ERROR parseando JSON: {e}")
        raise HTTPException(status_code=400, detail="JSON inválido")

    # ── 2. Logs de observabilidad ────────────────────────────────────────────
    import json as _json
    log.info(f"[ELEVEN TRIP] START")
    log.info(f"[ELEVEN TRIP] payload={_json.dumps(body, ensure_ascii=False)[:1000]}")

    # ── 3. Extraer campos — múltiples ubicaciones posibles ───────────────────
    tool_call_id = body.get("tool_call_id", "")
    tool_name    = body.get("tool_name", "")
    parameters   = body.get("parameters", {}) or {}

    # conversation_id puede venir en raíz o dentro de parameters
    conversation_id = (
        body.get("conversation_id")
        or parameters.get("conversation_id")
        or ""
    ).strip()

    # origen y destino: buscar en múltiples ubicaciones posibles
    # 1. parameters.origen / parameters.destino  (Webhook Tool estándar)
    # 2. body.origen / body.destino              (JSON simple de prueba)
    # 3. body.data.origen / body.data.destino    (payload anidado en data)
    data = body.get("data", {}) or {}

    origen = (
        parameters.get("origen")
        or body.get("origen")
        or data.get("origen")
        or ""
    ).strip()

    destino = (
        parameters.get("destino")
        or body.get("destino")
        or data.get("destino")
        or ""
    ).strip()

    date_programmed = (
        parameters.get("date_programmed")
        or body.get("date_programmed")
        or data.get("date_programmed")
        or ""
    ).strip() or None

    service_type_id = str(
        parameters.get("service_type_id")
        or body.get("service_type_id")
        or data.get("service_type_id")
        or "2"
    )

    whatsapp_user = (
        parameters.get("whatsapp_user")
        or body.get("whatsapp_user")
        or body.get("user_id")
        or data.get("whatsapp_user")
        or data.get("user_id")
        or ""
    ).strip()

    log.info(f"[ELEVEN TRIP] conversation_id={conversation_id!r}")
    log.info(f"[ELEVEN TRIP] origen={origen!r}")
    log.info(f"[ELEVEN TRIP] destino={destino!r}")
    log.info(f"[ELEVEN TRIP] tool_name={tool_name!r} tool_call_id={tool_call_id!r} whatsapp={whatsapp_user!r}")

    # ── 3. Validaciones ──────────────────────────────────────────────────────
    if not origen:
        log.warning("[ELEVEN TRIP] origen vacío")
        return {"result": "Error: el origen no puede estar vacío."}

    if not destino:
        log.warning("[ELEVEN TRIP] destino vacío")
        return {"result": "Error: el destino no puede estar vacío."}

    if not conversation_id:
        log.warning("[ELEVEN TRIP] sin conversation_id — generando uno de fallback")
        conversation_id = f"fallback_{tool_call_id or 'unknown'}"

    # ── 4. Idempotencia ──────────────────────────────────────────────────────
    existing = _get_existing_trip(conversation_id)
    if existing:
        log.info(
            f"[ELEVEN TRIP] duplicado ignorado — "
            f"conversation_id={conversation_id!r} "
            f"trip_id={existing['id']}"
        )
        return {
            "result": (
                f"Viaje ya registrado. "
                f"Origen: {existing['origen']} → Destino: {existing['destino']}."
            ),
            "tracking_url": _tracking_url(existing.get("tracking_token")),
        }

    # ── 5. Guardar el viaje ──────────────────────────────────────────────────
    try:
        trip_id, tracking_token = _save_trip(
            conversation_id=conversation_id,
            origen=origen,
            destino=destino,
            whatsapp_user=whatsapp_user,
            tool_call_id=tool_call_id,
        )
        log.info(f"[ELEVEN TRIP] DB INSERT trip_id={trip_id} conversation_id={conversation_id!r} origen={origen!r} destino={destino!r}")
    except Exception as e:
        log.error(f"[ELEVEN TRIP] ERROR guardando viaje: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error interno de base de datos")

    magiis_result = {"created": False, "disabled": True}
    if os.getenv("MAGIIS_WEB_FLOW_ENABLED", "0") == "1":
        magiis_result = _run_magiis_web(trip_id, origen, destino, date_programmed, service_type_id)

    if magiis_result.get("created"):
        _response = (
            f"Viaje pedido correctamente. Origen: {origen} → Destino: {destino}."
        )
    elif magiis_result.get("disabled"):
        _response = f"Viaje registrado pendiente de conexión. Origen: {origen} → Destino: {destino}."
    elif magiis_result.get("quote"):
        _response = f"Viaje cotizado y pendiente de confirmación. Origen: {origen} → Destino: {destino}."
    else:
        _response = f"Viaje registrado, pero no pudo enviarse a MAGIIS todavía. Origen: {origen} → Destino: {destino}."
    log.info(f"[ELEVEN TRIP] RESPONSE={_response!r}")
    log.info(f"[ELEVEN TRIP] END trip_id={trip_id} conversation_id={conversation_id!r}")

    # ── 6. Respuesta para ElevenLabs ──────────────────────────────────────────
    # ElevenLabs Webhook Tool requiere { "result": "..." }
    tracking_url = _tracking_url(tracking_token)
    return {
        "result": f"{_response} Link de seguimiento: {tracking_url}",
        "tracking_url": tracking_url,
        "trip_id": trip_id,
    }


@router.post("/cancel")
async def elevenlabs_cancel(request: Request):
    """Cancela el último viaje MAGIIS activo del pasajero de WhatsApp."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc

    parameters = body.get("parameters", {}) or {}
    data = body.get("data", {}) or {}
    whatsapp_user = (
        parameters.get("whatsapp_user")
        or body.get("whatsapp_user")
        or body.get("user_id")
        or data.get("whatsapp_user")
        or data.get("user_id")
        or ""
    ).strip()
    reason = (
        parameters.get("reason")
        or body.get("reason")
        or "Cancelación solicitada por el pasajero"
    ).strip()

    if not whatsapp_user:
        return {"result": "No pude identificar el número de WhatsApp del pasajero."}

    trip = _get_latest_active_trip(whatsapp_user)
    if not trip or not trip.get("magiis_travel_id"):
        return {"result": "No encontré un viaje activo para cancelar."}

    travel_id = int(trip["magiis_travel_id"])
    try:
        with httpx.Client(
            base_url=magiis_web.BASE_URL, timeout=magiis_web.TIMEOUT
        ) as client:
            session = magiis_web.login(client)
            response, _, _ = magiis_web.cancel_travel(
                session=session,
                travel_id=travel_id,
                operator_name=os.getenv("MAGIIS_WEB_OPERATOR_NAME", "Livre"),
                reason=reason,
                client=client,
            )
        response_body = response.json() if response.content else {}
        if response.status_code not in (200, 201, 204):
            raise RuntimeError(
                f"MAGIIS cancelación HTTP {response.status_code}: {str(response_body)[:500]}"
            )
        _update_magiis_state(
            trip["id"],
            status="cancelled",
            travel_id=travel_id,
            response=str(response_body)[:10000],
        )
        log.info(
            "[ELEVEN CANCEL] cancelled trip_id=%s magiis_travel_id=%s reason=%r",
            trip["id"], travel_id, reason,
        )
        return {"result": "El viaje fue cancelado correctamente."}
    except Exception as exc:
        log.error("[ELEVEN CANCEL] error: %s", exc, exc_info=True)
        _update_magiis_state(
            trip["id"],
            status="cancel_error",
            travel_id=travel_id,
            error=str(exc)[:500],
        )
        return {"result": "No pude cancelar el viaje en MAGIIS."}
