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
El registro y el seguimiento son propios de Livre; MAGIIS no participa.
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
ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/convai/whatsapp/outbound-message"
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "agent_3701m0dfvnmmemptqcm2jym9wdvv")
ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID = os.getenv(
    "ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID", "1364913613361192"
)
ELEVENLABS_WHATSAPP_TEMPLATE_NAME = os.getenv(
    "ELEVENLABS_WHATSAPP_TEMPLATE_NAME", "tracking_link"
)
ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE = os.getenv(
    "ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE", "es_AR"
)


def _tracking_url(token: str | None) -> str | None:
    return f"{TRACKING_BASE_URL}/?token={token}" if token else None


def send_tracking_message(trip: dict) -> str:
    """Envía el template aprobado al mismo usuario que originó el viaje."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    whatsapp_user = str(trip.get("whatsapp_user") or "").strip()
    tracking_url = _tracking_url(trip.get("tracking_token"))
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY no configurada")
    if not whatsapp_user:
        raise RuntimeError(f"El viaje {trip.get('id')} no tiene whatsapp_user")
    if not tracking_url:
        raise RuntimeError(f"El viaje {trip.get('id')} no tiene tracking_token")
    payload = {
        "whatsapp_phone_number_id": ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID,
        "whatsapp_user_id": whatsapp_user,
        "template_name": ELEVENLABS_WHATSAPP_TEMPLATE_NAME,
        "template_language_code": ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE,
        "template_params": [{"type": "body", "parameters": [{"text": tracking_url}]}],
        "agent_id": ELEVENLABS_AGENT_ID,
    }
    response = httpx.post(
        ELEVENLABS_API_URL,
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=20.0,
    )
    if response.is_error:
        log.error("[ELEVEN OUTBOUND] trip=%s status=%s body=%s", trip.get("id"), response.status_code, response.text[:500])
        response.raise_for_status()
    result = response.json()
    conversation_id = result.get("conversation_id") or ""
    log.info("[ELEVEN OUTBOUND] tracking enviado trip=%s conversation_id=%s", trip.get("id"), conversation_id)
    return conversation_id


def _whatsapp_user(body: dict, parameters: dict | None = None, data: dict | None = None) -> str:
    """Obtiene la identidad de WhatsApp del contrato de LIVI/ElevenLabs."""
    parameters = parameters or body.get("parameters", {}) or {}
    data = data or body.get("data", {}) or {}
    candidates = (
        parameters.get("system__caller_id"),
        parameters.get("whatsapp_user"),
        parameters.get("user_id"),
        body.get("system__caller_id"),
        body.get("whatsapp_user"),
        body.get("user_id"),
        data.get("system__caller_id"),
        data.get("whatsapp_user"),
        data.get("user_id"),
        (data.get("metadata") or {}).get("system__caller_id"),
    )
    return next((str(value).strip() for value in candidates if value), "")


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
        # Keep ElevenLabs on the same assigned-trip contract consumed by the driver app.
        cur.execute(
            """
            SELECT u.id, u.name
            FROM livre_users u
            JOIN livre_driver_accounts a ON a.user_id = u.id
            WHERE a.active = TRUE
            ORDER BY a.created_at ASC, u.created_at ASC
            LIMIT 1
            """
        )
        driver = cur.fetchone()
        if not driver:
            raise RuntimeError("No hay un conductor Livre disponible")
        cur.execute(
            """
            INSERT INTO livi_trips
                (conversation_id, origen, destino, whatsapp_user, tool_call_id,
                 tracking_token, assigned_driver_user_id, tracking_driver_name,
                 status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'assigned', %s, %s)
            RETURNING id;
            """,
            (
                conversation_id,
                origen,
                destino,
                whatsapp_user or "",
                tool_call_id or "",
                tracking_token,
                driver["id"],
                driver["name"],
                datetime.now(timezone.utc),
                datetime.now(timezone.utc),
            ),
        )
        return cur.fetchone()["id"], tracking_token


def _get_latest_active_trip(whatsapp_user: str) -> dict | None:
    """Busca el último viaje activo registrado por Livre."""
    if not whatsapp_user:
        return None
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT * FROM livi_trips
            WHERE whatsapp_user = %s
              AND status IN ('pending', 'assigned', 'accepted', 'in_progress')
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
        return {
            "price": next(item["price"] for item in options if item["transportTypeId"] == "2"),
            "options": [
                item for item in options
                if item["transportTypeId"] in {"1", "2"}
            ],
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
    options = sorted(
        quote.get("options", []),
        key=lambda option: {"1": 1, "2": 2}.get(str(option.get("transportTypeId")), 99),
    )[:2]
    option_text = " ".join(
        f"Opción {index} ({option.get('serviceName', 'servicio')}): ${option['price']}."
        for index, option in enumerate(options, 1)
    )
    return {
        "result": f"Tengo dos cotizaciones. {option_text}",
        "options": options,
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
    whatsapp_user = _whatsapp_user(body, parameters, data)
    if not whatsapp_user:
        return {"result": "No pude identificar al pasajero de WhatsApp."}

    trip = _get_latest_active_trip(whatsapp_user)
    if not trip:
        return {"result": "No encontré un viaje activo para este pasajero."}

    driver = trip.get("tracking_driver_name")
    vehicle_parts = [
        trip.get("tracking_vehicle_model"),
        trip.get("tracking_vehicle_color"),
    ]
    vehicle = " ".join(part for part in vehicle_parts if part).strip()
    plate = trip.get("tracking_plate")

    tracking_url = _tracking_url(trip.get("tracking_token"))
    latitude = trip.get("tracking_latitude")
    longitude = trip.get("tracking_longitude")
    location_url = (
        f"https://www.google.com/maps?q={latitude},{longitude}"
        if latitude is not None and longitude is not None
        else None
    )

    if not driver and not vehicle and not plate:
        result = "El viaje está registrado y todavía estamos buscando un conductor."
        if location_url:
            result += f" Última ubicación conocida del vehículo: {location_url}"
        elif tracking_url:
            result += f" Seguimiento: {tracking_url}"
        return {
            "result": result,
            "status": trip.get("status"),
            "tracking_url": tracking_url,
            "location": {"latitude": latitude, "longitude": longitude} if location_url else None,
            "location_url": location_url,
        }

    assignment = []
    if driver:
        assignment.append(f"conductor {driver}")
    if vehicle:
        assignment.append(f"vehículo {vehicle}")
    if plate:
        assignment.append(f"patente {plate}")
    result = "Ya hay una asignación: " + ", ".join(assignment) + "."
    if location_url:
        result += f" Ubicación del vehículo: {location_url}"
    elif tracking_url:
        result += f" Seguimiento: {tracking_url}"
    return {
        "result": result,
        "status": trip.get("status"),
        "driver": driver or None,
        "vehicle": vehicle or None,
        "plate": plate or None,
        "tracking_url": tracking_url,
        "location": {"latitude": latitude, "longitude": longitude} if location_url else None,
        "location_url": location_url,
    }


@router.get("/tracking/{tracking_token}")
def passenger_tracking(tracking_token: str):
    """Devuelve sólo los datos públicos mínimos del viaje para el mapa."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, origen, destino, status,
                   tracking_driver_name, tracking_plate,
                   tracking_vehicle_color, tracking_vehicle_model,
                   tracking_latitude, tracking_longitude,
                   tracking_location_updated_at, tracking_location_visible_at
            FROM livi_trips
            WHERE tracking_token = %s
            LIMIT 1;
            """,
            [tracking_token],
        )
        trip = cur.fetchone()
    if not trip:
        raise HTTPException(status_code=404, detail="Seguimiento no encontrado")

    driver = trip.get("tracking_driver_name") or None
    vehicle_parts = [
        trip.get("tracking_vehicle_model"),
        trip.get("tracking_vehicle_color"),
    ]
    vehicle = " ".join(part for part in vehicle_parts if part).strip() or None
    plate = trip.get("tracking_plate")
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
    # Se devuelve la última ubicación conocida junto con su fecha. El pasajero
    # debe poder verla aunque el siguiente pulso del conductor se demore.
    return {
        "trip_id": trip["id"],
        "origin": trip["origen"],
        "destination": trip["destino"],
        "status": trip.get("status"),
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

    whatsapp_user = _whatsapp_user(body, parameters, data)

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

    _response = f"Viaje registrado en Livre y asignado a un conductor. Origen: {origen} → Destino: {destino}."
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
    """Cancela el último viaje activo registrado por Livre."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc

    parameters = body.get("parameters", {}) or {}
    data = body.get("data", {}) or {}
    whatsapp_user = _whatsapp_user(body, parameters, data)
    if not whatsapp_user:
        return {"result": "No pude identificar el número de WhatsApp del pasajero."}

    trip = _get_latest_active_trip(whatsapp_user)
    if not trip:
        return {"result": "No encontré un viaje activo para cancelar."}

    with get_cursor() as cur:
        cur.execute(
            "UPDATE livi_trips SET status='cancelled', updated_at=NOW() WHERE id=%s",
            [trip["id"]],
        )
    log.info("[ELEVEN CANCEL] cancelled local trip_id=%s", trip["id"])
    return {"result": "El viaje fue cancelado correctamente en Livre."}
