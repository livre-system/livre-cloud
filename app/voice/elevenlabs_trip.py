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
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request, HTTPException
from app.core.database import get_cursor
from app.integrations import magiis_web

log = logging.getLogger(__name__)

router = APIRouter(prefix="/elevenlabs", tags=["elevenlabs"])


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
) -> int:
    """Inserta el viaje y devuelve el id generado."""
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO livi_trips
                (conversation_id, origen, destino, whatsapp_user, tool_call_id, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)
            RETURNING id;
            """,
            (
                conversation_id,
                origen,
                destino,
                whatsapp_user or "",
                tool_call_id or "",
                datetime.now(timezone.utc),
                datetime.now(timezone.utc),
            ),
        )
        return cur.fetchone()["id"]


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
                 if str(item.get("transportTypeId")) == "2"
                 and item.get("serviceStatus") == "AVAILABLE"),
                None,
            )
            if selected is None:
                raise RuntimeError("MAGIIS Web no ofreció transportTypeId=2 disponible")

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
            )
        }

    # ── 5. Guardar el viaje ──────────────────────────────────────────────────
    try:
        trip_id = _save_trip(
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
        magiis_result = _run_magiis_web(trip_id, origen, destino)

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
    return {"result": _response}
