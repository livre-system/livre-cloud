"""
app/voice/elevenlabs_postcall.py

Endpoint POST /elevenlabs/post-call
Recibe post_call_transcription de ElevenLabs al finalizar una conversación de LIVI.
Guarda cada turno del transcript en memoria de Livre Cloud con embedding automático.

Estructura real del payload (verificada con debug real):
{
  "type": "post_call_transcription",
  "event_timestamp": 1788692487,
  "conversation_id": "conv_...",        ← también en raíz
  "data": {
    "conversation_id": "conv_...",
    "agent_id": "agent_...",
    "user_id": "5491178240367",         ← número WhatsApp del cliente
    "status": "done",
    "metadata": {
      "start_time_unix_secs": ...,
      "call_duration_secs": 40
    }
  },
  "transcript": [                       ← array directo en body, NO en data
    {
      "role": "user" | "agent",
      "message": "...",                 ← puede ser null en turnos de tool_calls
      "time_in_call_secs": 0,
      "id": "af066bee-..."             ← UUID único por turno — usado para idempotencia
    }
  ]
}

Idempotencia: usa turn.id (UUID por turno) para evitar duplicados en reintentos.
Firma: valida header elevenlabs-signature con ELEVENLABS_WEBHOOK_SECRET si está configurado.
"""

import os
import hmac
import hashlib
import logging
import threading
from fastapi import APIRouter, Request, HTTPException
from app.memory.repository import (
    user_exists,
    create_user,
    get_or_create_document,
    add_row_without_embedding,
    update_embedding,
)
from app.memory.embeddings import get_embedding
from app.core.database import get_cursor

log = logging.getLogger(__name__)

router = APIRouter(prefix="/elevenlabs", tags=["elevenlabs"])

ELEVENLABS_SYSTEM_USER  = "elevenlabs_voice"
ELEVENLABS_WEBHOOK_SECRET = os.getenv("ELEVENLABS_WEBHOOK_SECRET", "")


# ─────────────────────────────────────────────────────────────────────────────
# Validación de firma
# ─────────────────────────────────────────────────────────────────────────────

def _verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Valida la firma de ElevenLabs.
    Header: elevenlabs-signature: t=TIMESTAMP,v0=HMAC_SHA256
    Si ELEVENLABS_WEBHOOK_SECRET no está configurado, pasa sin validar.
    """
    if not ELEVENLABS_WEBHOOK_SECRET:
        log.warning("[ELEVEN POSTCALL] ELEVENLABS_WEBHOOK_SECRET no configurado — firma no validada")
        return True

    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        timestamp = parts.get("t", "")
        v0        = parts.get("v0", "")
        signed_payload = f"{timestamp}.{raw_body.decode('utf-8')}"
        expected = hmac.new(
            ELEVENLABS_WEBHOOK_SECRET.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, v0)
    except Exception as e:
        log.error(f"[ELEVEN POSTCALL] Error validando firma: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Idempotencia por turn.id
# ─────────────────────────────────────────────────────────────────────────────

def _turn_already_saved(doc_key: str, turn_id: str) -> bool:
    """Verifica si un turno ya fue guardado usando su UUID."""
    if not turn_id:
        return False
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT mr.id FROM memory_rows mr
                JOIN memory_documents md ON md.id = mr.document_id
                WHERE md.user_id = %s
                  AND md.key = %s
                  AND mr.data->>'turn_id' = %s
                LIMIT 1
            """, [ELEVENLABS_SYSTEM_USER, doc_key, turn_id])
            return cur.fetchone() is not None
    except Exception as e:
        log.warning(f"[ELEVEN POSTCALL] no se pudo verificar idempotencia: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Embedding en background
# ─────────────────────────────────────────────────────────────────────────────

def _embed_rows(rows_to_embed: list[tuple[str, str]], conversation_id: str):
    """Genera embeddings para todas las filas. Corre en thread separado."""
    ok = 0
    for row_id, message in rows_to_embed:
        try:
            emb = get_embedding(message)
            if emb:
                update_embedding(row_id, emb)
                log.info(
                    f"[ELEVEN POSTCALL] embedding OK — "
                    f"row_id={row_id} dims={len(emb)}"
                )
                ok += 1
            else:
                log.warning(f"[ELEVEN POSTCALL] embedding None — row_id={row_id}")
        except Exception as e:
            log.error(f"[ELEVEN POSTCALL] ERROR embedding row_id={row_id}: {e}")

    log.info(
        f"[ELEVEN POSTCALL] embeddings OK — "
        f"conversation_id={conversation_id!r} "
        f"procesados={ok}/{len(rows_to_embed)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint principal
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/post-call")
async def elevenlabs_post_call(request: Request):
    """
    Recibe post_call_transcription de ElevenLabs.
    Guarda cada turno con mensaje en memoria + embedding automático.
    Responde HTTP 200 inmediatamente. Embeddings van en background.
    """
    # ── 1. Leer body raw ────────────────────────────────────────────────────
    try:
        raw_body = await request.body()
    except Exception as e:
        log.error(f"[ELEVEN POSTCALL] ERROR leyendo body: {e}")
        raise HTTPException(status_code=400, detail="No se pudo leer el body")

    # ── 2. Validar firma ────────────────────────────────────────────────────
    sig_header = request.headers.get("elevenlabs-signature", "")
    if not ELEVENLABS_WEBHOOK_SECRET:
        log.error("[ELEVEN POSTCALL] ELEVENLABS_WEBHOOK_SECRET no configurado — request rechazado")
        raise HTTPException(status_code=503, detail="Webhook de ElevenLabs no configurado")
    if not sig_header:
        raise HTTPException(status_code=401, detail="Firma requerida")
    if not _verify_signature(raw_body, sig_header):
        log.error("[ELEVEN POSTCALL] Firma inválida — request rechazado")
        raise HTTPException(status_code=401, detail="Firma inválida")

    # ── 3. Parsear JSON ─────────────────────────────────────────────────────
    try:
        import json
        body = json.loads(raw_body)
    except Exception as e:
        log.error(f"[ELEVEN POSTCALL] ERROR parseando JSON: {e}")
        raise HTTPException(status_code=400, detail="JSON inválido")

    # ── 4. Extraer campos — estructura real verificada ───────────────────────
    event_type  = body.get("type", "")
    data        = body.get("data", {}) or {}

    # conversation_id está tanto en body como en body.data
    conversation_id = (
        body.get("conversation_id")
        or data.get("conversation_id")
        or ""
    )
    agent_id        = data.get("agent_id", "")
    whatsapp_user   = (
        data.get("system__caller_id")
        or data.get("user_id")
        or data.get("whatsapp_user")
        or body.get("system__caller_id")
        or body.get("whatsapp_user")
        or (data.get("metadata") or {}).get("system__caller_id")
        or ""
    )   # identificador persistente del pasajero de WhatsApp
    status          = data.get("status", "")
    metadata        = data.get("metadata", {}) or {}
    duration_secs   = metadata.get("call_duration_secs", 0)

    # transcript está directo en body, NO en data
    transcript = body.get("transcript", []) or []

    log.info(
        f"[ELEVEN POSTCALL] recibido — "
        f"type={event_type!r} status={status!r} "
        f"conversation_id={conversation_id!r} "
        f"agent_id={agent_id!r} "
        f"whatsapp_user={whatsapp_user!r} "
        f"mensajes={len(transcript)} "
        f"duracion={duration_secs}s"
    )

    # ── 5. Validaciones ─────────────────────────────────────────────────────
    if not conversation_id:
        log.warning("[ELEVEN POSTCALL] sin conversation_id — ignorado")
        return {"ok": True, "detail": "sin conversation_id"}

    if not transcript:
        log.warning(f"[ELEVEN POSTCALL] transcript vacío — conversation_id={conversation_id!r}")
        return {"ok": True, "detail": "transcript vacío"}

    log.info(f"[ELEVEN POSTCALL] conversation_id={conversation_id!r}")
    log.info(f"[ELEVEN POSTCALL] mensajes={len(transcript)} duracion={duration_secs}s")

    # ── 6. Crear usuario de sistema si no existe ─────────────────────────────
    if not user_exists(ELEVENLABS_SYSTEM_USER):
        create_user(ELEVENLABS_SYSTEM_USER)
        log.info(f"[ELEVEN POSTCALL] usuario creado — {ELEVENLABS_SYSTEM_USER}")

    # ── 7. Documento único por conversation_id ───────────────────────────────
    doc_key  = f"livi_{conversation_id}"
    doc_name = f"LIVI {conversation_id[:16]}"
    doc_desc = (
        f"Conversación LIVI — "
        f"conversation_id={conversation_id} "
        f"whatsapp={whatsapp_user} "
        f"agent={agent_id} "
        f"duracion={duration_secs}s"
    )

    document_id, doc_created = get_or_create_document(
        ELEVENLABS_SYSTEM_USER, doc_key, doc_name, doc_desc
    )
    if doc_created:
        log.info(f"[ELEVEN POSTCALL] nuevo documento — key={doc_key!r}")

    # ── 8. Guardar cada turno ────────────────────────────────────────────────
    saved         = 0
    skipped       = 0
    failed        = 0
    rows_to_embed = []   # (row_id, message)

    for idx, turn in enumerate(transcript):
        role    = turn.get("role", "user")
        message = (turn.get("message") or "").strip()
        secs    = turn.get("time_in_call_secs", 0)
        turn_id = turn.get("id", "")   # UUID único por turno

        # Solo guardar turnos con mensaje real (ignorar tool_calls/tool_results)
        if not message:
            skipped += 1
            continue

        # Normalizar role: ElevenLabs usa "agent", Livre usa "assistant"
        normalized_role = "assistant" if role == "agent" else "user"

        # Idempotencia: si este turno ya existe no duplicar
        if turn_id and _turn_already_saved(doc_key, turn_id):
            log.info(f"[ELEVEN POSTCALL] turno {idx} ya existe — turn_id={turn_id!r}")
            skipped += 1
            continue

        log.info(
            f"[ELEVEN POSTCALL] turno {idx} | "
            f"role={normalized_role} | "
            f"secs={secs} | "
            f"chars={len(message)} | "
            f"preview={message[:80]!r}"
        )

        data_row = {
            "message":         message,
            "role":            normalized_role,
            "conversation_id": conversation_id,
            "agent_id":        agent_id,
            "whatsapp_user":   whatsapp_user,
            "turn_id":         turn_id,
            "time_in_call_secs": secs,
            "turn_index":      idx,
        }

        try:
            row_id, _ = add_row_without_embedding(document_id, data_row)
            rows_to_embed.append((row_id, message))
            log.info(
                f"[ELEVEN POSTCALL] guardado — "
                f"row_id={row_id} turno={idx} role={normalized_role}"
            )
            saved += 1
        except Exception as e:
            log.error(f"[ELEVEN POSTCALL] ERROR guardando turno {idx}: {e}", exc_info=True)
            failed += 1

    log.info(
        f"[ELEVEN POSTCALL] guardado — "
        f"conversation_id={conversation_id!r} "
        f"guardados={saved} saltados={skipped} fallidos={failed}"
    )

    # ── 9. Embeddings en background ──────────────────────────────────────────
    if rows_to_embed:
        threading.Thread(
            target=_embed_rows,
            args=(rows_to_embed, conversation_id),
            daemon=True,
        ).start()
        log.info(
            f"[ELEVEN POSTCALL] embeddings iniciados en background — "
            f"filas={len(rows_to_embed)}"
        )

    # ── 10. Respuesta inmediata a ElevenLabs ─────────────────────────────────
    return {
        "ok":              True,
        "conversation_id": conversation_id,
        "guardados":       saved,
        "saltados":        skipped,
        "fallidos":        failed,
    }
