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
NO integra MAGIIS todavía. Solo registra el pedido.
"""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from app.core.database import get_cursor

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

    # ── 2. Extraer campos del payload de ElevenLabs Webhook Tool ─────────────
    tool_call_id = body.get("tool_call_id", "")
    tool_name    = body.get("tool_name", "")
    parameters   = body.get("parameters", {}) or {}

    # conversation_id puede venir en raíz o dentro de parameters
    conversation_id = (
        body.get("conversation_id")
        or parameters.get("conversation_id")
        or ""
    ).strip()

    # origen y destino vienen en parameters
    origen  = (parameters.get("origen") or "").strip()
    destino = (parameters.get("destino") or "").strip()

    # whatsapp_user puede venir opcionalmente
    whatsapp_user = (parameters.get("whatsapp_user") or body.get("user_id") or "").strip()

    log.info(
        f"[ELEVEN TRIP] recibido — "
        f"tool_name={tool_name!r} "
        f"conversation_id={conversation_id!r} "
        f"origen={origen!r} "
        f"destino={destino!r} "
        f"whatsapp={whatsapp_user!r}"
    )

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
        log.info(
            f"[ELEVEN TRIP] guardado — "
            f"trip_id={trip_id} "
            f"conversation_id={conversation_id!r} "
            f"origen={origen!r} "
            f"destino={destino!r}"
        )
    except Exception as e:
        log.error(f"[ELEVEN TRIP] ERROR guardando viaje: {e}", exc_info=True)
        return {"result": "Error interno al registrar el viaje. Por favor intentá de nuevo."}

    log.info(f"[ELEVEN TRIP] completo — trip_id={trip_id}")

    # ── 6. Respuesta para ElevenLabs ─────────────────────────────────────────
    # ElevenLabs Webhook Tool requiere { "result": "..." }
    return {
        "result": (
            f"Viaje registrado correctamente. "
            f"Origen: {origen} → Destino: {destino}."
        )
    }
