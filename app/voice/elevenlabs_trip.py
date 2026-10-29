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

import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request, HTTPException
from app.core.database import get_cursor
from app.integrations.google_maps import geocode_address, route_distance_km
from app.integrations.historical_quote import (
    FARE_SOURCE_COMMERCIAL,
    apply_commercial_discount,
    commercial_fare,
    historical_options,
)

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
ELEVENLABS_WHATSAPP_ARRIVING_TEMPLATE_NAME = os.getenv(
    "ELEVENLABS_WHATSAPP_ARRIVING_TEMPLATE_NAME", ""
).strip()
ELEVENLABS_WHATSAPP_REJECTION_TEMPLATE_NAME = os.getenv(
    "ELEVENLABS_WHATSAPP_REJECTION_TEMPLATE_NAME", ""
).strip()
ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE = os.getenv(
    "ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE", "es_AR"
)
ARRIVING_MESSAGE = "Tu viaje Livre fue tomado. El conductor ya está en camino."
REJECTION_MESSAGE = "El conductor no pudo tomar tu viaje. Estamos buscando otra opción."


def send_rejection_message(trip: dict) -> str:
    """Avisa al pasajero que el conductor rechazó el viaje."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    whatsapp_user = str(trip.get("whatsapp_user") or "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY no configurada")
    if not whatsapp_user:
        raise RuntimeError(f"El viaje {trip.get('id')} no tiene whatsapp_user")
    if not ELEVENLABS_WHATSAPP_REJECTION_TEMPLATE_NAME:
        raise RuntimeError(
            "Bloqueado: falta ELEVENLABS_WHATSAPP_REJECTION_TEMPLATE_NAME"
        )
    payload = {
        "whatsapp_phone_number_id": ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID,
        "whatsapp_user_id": whatsapp_user,
        "template_name": ELEVENLABS_WHATSAPP_REJECTION_TEMPLATE_NAME,
        "template_language_code": ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE,
        "template_params": [{"type": "body", "parameters": [{"text": REJECTION_MESSAGE}]}],
        "agent_id": ELEVENLABS_AGENT_ID,
    }
    response = httpx.post(
        ELEVENLABS_API_URL,
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=20.0,
    )
    if response.is_error:
        log.error("[ELEVEN OUTBOUND] rechazo trip=%s status=%s body=%s", trip.get("id"), response.status_code, response.text[:500])
        response.raise_for_status()
    result = response.json()
    conversation_id = result.get("conversation_id") or ""
    log.info("[ELEVEN OUTBOUND] rechazo enviado trip=%s conversation_id=%s", trip.get("id"), conversation_id)
    return conversation_id


def _tracking_url(token: str | None) -> str | None:
    return f"{TRACKING_BASE_URL}/?token={token}" if token else None


def send_tracking_message(trip: dict) -> str:
    """Envía el aviso aprobado al mismo usuario que originó el viaje."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    whatsapp_user = str(trip.get("whatsapp_user") or "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY no configurada")
    if not whatsapp_user:
        raise RuntimeError(f"El viaje {trip.get('id')} no tiene whatsapp_user")
    if not ELEVENLABS_WHATSAPP_ARRIVING_TEMPLATE_NAME:
        raise RuntimeError(
            "Bloqueado: falta ELEVENLABS_WHATSAPP_ARRIVING_TEMPLATE_NAME; "
            "Meta debe aprobar/configurar una plantilla para el aviso assigned→arriving"
        )
    payload = {
        "whatsapp_phone_number_id": ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID,
        "whatsapp_user_id": whatsapp_user,
        "template_name": ELEVENLABS_WHATSAPP_ARRIVING_TEMPLATE_NAME,
        "template_language_code": ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE,
        "template_params": [{"type": "body", "parameters": [{"text": ARRIVING_MESSAGE}]}],
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
    log.info("[ELEVEN OUTBOUND] aviso assigned→arriving enviado trip=%s conversation_id=%s", trip.get("id"), conversation_id)
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


def _save_whatsapp_quote(
    *, conversation_id: str, whatsapp_user: str, origen: str, destino: str,
    options: list[dict],
) -> str:
    quote_id = str(uuid.uuid4())
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO livi_whatsapp_quotes
                (quote_id, conversation_id, whatsapp_user, origen, destino, options)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (quote_id, conversation_id, whatsapp_user, origen, destino, json.dumps(options)),
        )
    return quote_id


def _get_whatsapp_quote(quote_id: str, conversation_id: str, whatsapp_user: str) -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT quote_id, origen, destino, options
            FROM livi_whatsapp_quotes
            WHERE quote_id = %s AND conversation_id = %s AND whatsapp_user = %s
              AND source = 'whatsapp' AND consumed_at IS NULL AND expires_at > NOW()
            FOR UPDATE
            """,
            (quote_id, conversation_id, whatsapp_user),
        )
        return cur.fetchone()


def _save_trip(
    conversation_id: str,
    origen: str,
    destino: str,
    whatsapp_user: str = "",
    tool_call_id: str = "",
    service_type_id: str = "2",
    fare: Decimal | int | None = None,
    service_type_name: str | None = None,
    quote_id: str | None = None,
    original_fare: Decimal | None = None,
    discount_percentage: Decimal | None = None,
    discount_amount: Decimal | None = None,
    final_fare: Decimal | None = None,
    fare_source: str | None = None,
    activity_type: str = "real",
    source: str = "whatsapp",
    status: str = "searching",
    scheduled_at: datetime | None = None,
) -> tuple[int, str]:
    """Inserta el viaje con la distribución comercial canónica de Livre."""
    if original_fare is not None or fare is not None:
        official_fare = original_fare if original_fare is not None else Decimal(str(fare))
        financials = commercial_fare(official_fare, service_type_id=service_type_id)
        fare = financials["original_fare"]
        original_fare = financials["original_fare"]
        discount_percentage = financials["discount_percentage"]
        discount_amount = financials["discount_amount"]
        final_fare = financials["final_fare"]
        fare_source = FARE_SOURCE_COMMERCIAL
    tracking_token = secrets.token_urlsafe(32)
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO livi_trips
                (conversation_id, origen, destino, whatsapp_user, tool_call_id,
                 tracking_token, assigned_driver_user_id, tracking_driver_name,
                 service_type_id, service_type_name, fare, quote_id,
                 original_fare, discount_percentage, discount_amount, final_fare, fare_source,
                 activity_type, source, status, scheduled_at, created_at, updated_at)
                 VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                conversation_id,
                origen,
                destino,
                whatsapp_user or "",
                tool_call_id or "",
                tracking_token,
                service_type_id,
                service_type_name,
                fare,
                quote_id,
                original_fare,
                discount_percentage,
                discount_amount,
                final_fare,
                fare_source,
                activity_type,
                source,
                status,
                scheduled_at,
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
              AND status IN ('pending', 'searching', 'scheduled', 'assigned', 'accepted',
                             'arriving', 'in_progress', 'conflict')
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            [whatsapp_user],
        )
        return cur.fetchone()


def _trip_status_message(trip: dict) -> str:
    messages = {
        "pending": "El viaje fue recibido y está por entrar en asignación.",
        "searching": "Estamos buscando un vehículo disponible para tu viaje.",
        "scheduled": "Tu viaje quedó programado.",
        "assigned": "Tu viaje fue tomado por un conductor.",
        "arriving": "Tu conductor está en camino.",
        "in_progress": "Tu viaje está en curso.",
        "conflict": "Tuvimos un inconveniente con tu viaje y lo estamos resolviendo.",
        "lost": "No encontramos un vehículo disponible para realizar tu viaje.",
        "cancelled": "Tu viaje fue cancelado.",
        "completed": "Tu viaje finalizó. Gracias por viajar con Livre.",
    }
    return messages.get(str(trip.get("status")), "Estamos actualizando tu viaje.")


def _run_local_quote(origen: str, destino: str) -> dict:
    """Cotiza con perfiles propios de Livre; no consulta proveedores externos."""
    try:
        origin = geocode_address(origen)
        destination = geocode_address(destino)
        distance_km = route_distance_km(origin, destination)
        # WhatsApp/LIVI comunica el total bruto al pasajero. La distribución
        # 85/15 se calcula únicamente al confirmar y persistir el viaje.
        options = [
            item
            for item in historical_options(Decimal(str(distance_km)))
            if str(item.get("transportTypeId")) in {"1", "2"}
        ]
        return {
            "price": next(item["price"] for item in options if item["transportTypeId"] == "2"),
            "options": options,
            "locations": {"origin": origin, "destination": destination},
            "distance_km": distance_km,
            "source": "livre_quote_profiles",
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
    whatsapp_user = _whatsapp_user(body, parameters, data)
    conversation_id = str(
        body.get("conversation_id")
        or parameters.get("conversation_id")
        or data.get("conversation_id")
        or body.get("tool_call_id")
        or uuid.uuid4()
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
    options = [option for option in sorted(
        quote.get("options", []),
        key=lambda option: {"1": 1, "2": 2}.get(str(option.get("transportTypeId")), 99),
    )[:2]]
    option_text = " ".join(
        f"Opción {index} ({option.get('serviceName', 'servicio')}): ${option['pricePointToPoint']}."
        for index, option in enumerate(options, 1)
    )
    quote_id = _save_whatsapp_quote(
        conversation_id=conversation_id,
        whatsapp_user=whatsapp_user,
        origen=origen,
        destino=destino,
        options=options,
    )
    return {
        "result": f"Tengo dos cotizaciones. {option_text}",
        "options": options,
        "quote_id": quote_id,
        "conversation_id": conversation_id,
        "source": "whatsapp",
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
            "status_message": _trip_status_message(trip),
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
        "status_message": _trip_status_message(trip),
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

    selected_fare = (
        parameters.get("fare")
        or parameters.get("price")
        or body.get("fare")
        or body.get("price")
        or data.get("fare")
        or data.get("price")
    )
    quote_id = str(
        parameters.get("quote_id")
        or body.get("quote_id")
        or data.get("quote_id")
        or ""
    ).strip() or None

    whatsapp_user = _whatsapp_user(body, parameters, data)
    activity_type = str(
        parameters.get("activity_type")
        or body.get("activity_type")
        or data.get("activity_type")
        or "real"
    ).strip()
    source = str(
        parameters.get("source")
        or body.get("source")
        or data.get("source")
        or "whatsapp"
    ).strip()
    if activity_type not in {"real", "test"}:
        return {"result": "Error: activity_type debe ser real o test."}
    if not source:
        return {"result": "Error: source no puede estar vacío."}

    scheduled_at = None
    status = "searching"
    if date_programmed:
        try:
            scheduled_at = datetime.fromisoformat(date_programmed)
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(
                    tzinfo=ZoneInfo("America/Argentina/Buenos_Aires")
                )
            scheduled_at = scheduled_at.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return {"result": "Error: fecha programada inválida."}
        if scheduled_at <= datetime.now(timezone.utc):
            return {"result": "Error: la fecha programada debe ser futura."}
        status = "scheduled"

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
        quote_snapshot = _get_whatsapp_quote(quote_id, conversation_id, whatsapp_user) if quote_id else None
        if quote_id and not quote_snapshot:
            raise RuntimeError("La cotización de WhatsApp no existe, venció o ya fue usada")
        if quote_snapshot:
            quote_options = quote_snapshot["options"]
            selected = next(
                (item for item in quote_options
                 if str(item.get("transportTypeId")) == service_type_id),
                None,
            )
            quote = {"options": quote_options}
        else:
            quote = _run_local_quote(origen, destino)
            if quote.get("error"):
                raise RuntimeError(f"No se pudo reconstruir la cotización confirmada: {quote['error']}")
            selected = next(
                (item for item in quote["options"]
                 if str(item.get("transportTypeId")) == service_type_id),
                None,
            )
        if selected is None:
            raise RuntimeError(f"Tipo de servicio no cotizado: {service_type_id}")

        # El snapshot de cotización contiene el total bruto. Aplicar el split
        # aquí mantiene el 85% para el conductor sin descontarlo en WhatsApp.
        financials = apply_commercial_discount(selected)
        final_fare = Decimal(str(financials["finalFare"]))
        original_fare = Decimal(str(financials["originalFare"]))
        discount_percentage = Decimal(str(financials["discountPercentage"]))
        discount_amount = Decimal(str(financials["discountAmount"]))
        fare_value = original_fare
        if selected_fare is not None and Decimal(str(selected_fare).replace("$", "").replace(",", "").strip()) != original_fare:
            raise RuntimeError(
                f"La tarifa confirmada no coincide con la cotización: {selected_fare} != {original_fare}"
            )
        service_type_name = {"1": "Premium", "2": "Ahorro"}.get(
            service_type_id, selected.get("serviceName")
        )
        trip_id, tracking_token = _save_trip(
            conversation_id=conversation_id,
            origen=origen,
            destino=destino,
            whatsapp_user=whatsapp_user,
            tool_call_id=tool_call_id,
            service_type_id=service_type_id,
            fare=fare_value,
            service_type_name=service_type_name,
            quote_id=quote_id,
            original_fare=original_fare,
            discount_percentage=discount_percentage,
            discount_amount=discount_amount,
            final_fare=final_fare,
            fare_source="official",
            activity_type=activity_type,
            source=source,
            status=status,
            scheduled_at=scheduled_at,
        )
        if quote_id:
            with get_cursor() as cur:
                cur.execute(
                    "UPDATE livi_whatsapp_quotes SET consumed_at = NOW() WHERE quote_id = %s",
                    (quote_id,),
                )
        log.info(f"[ELEVEN TRIP] quote consumed locally quote_id={quote_id!r}")
        log.info(f"[ELEVEN TRIP] DB INSERT trip_id={trip_id} conversation_id={conversation_id!r} origen={origen!r} destino={destino!r}")
    except Exception as e:
        log.error(f"[ELEVEN TRIP] ERROR guardando viaje: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error interno de base de datos")

    _response = f"Viaje registrado en Livre y buscando conductor. Origen: {origen} → Destino: {destino}."
    log.info(f"[ELEVEN TRIP] RESPONSE={_response!r}")
    log.info(f"[ELEVEN TRIP] END trip_id={trip_id} conversation_id={conversation_id!r}")

    # ── 6. Respuesta para ElevenLabs ──────────────────────────────────────────
    # ElevenLabs Webhook Tool requiere { "result": "..." }
    tracking_url = _tracking_url(tracking_token)
    return {
        "result": _response,
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
