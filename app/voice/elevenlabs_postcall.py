"""
app/voice/elevenlabs_postcall.py

Endpoint que recibe el webhook post_call_transcription de ElevenLabs.
Cuando LIVI termina una conversación, ElevenLabs envía el transcript completo.

Payload de ElevenLabs post_call_transcription:
{
  "type": "post_call_transcription",
  "event_id": "...",
  "conversation_id": "...",
  "agent_id": "...",
  "status": "done",
  "transcript": [
    { "role": "agent", "message": "...", "time_in_call_secs": 0 },
    { "role": "user",  "message": "...", "time_in_call_secs": 3 }
  ],
  "metadata": { "start_time_unix_secs": ..., "call_duration_secs": ... },
  "analysis": {}
}

Registro en ElevenLabs:
  URL:    https://livre-cloud-production.up.railway.app/elevenlabs/post-call
  Evento: post_call_transcription
  Método: POST
"""

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

ELEVENLABS_SYSTEM_USER = "elevenlabs_voice"


def _already_processed(conversation_id: str, event_id: str) -> bool:
    """Verifica si esta conversación ya fue procesada (idempotencia)."""
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT mr.id FROM memory_rows mr
                JOIN memory_documents md ON md.id = mr.document_id
                WHERE md.user_id = %s
                  AND md.key = %s
                  AND mr.data->>'event_id' = %s
                LIMIT 1
            """, [ELEVENLABS_SYSTEM_USER, f"livi_{conversation_id}", event_id])
            return cur.fetchone() is not None
    except Exception as e:
        log.warning(f"[ELEVEN POSTCALL] no se pudo verificar idempotencia: {e}")
        return False


def _generate_embedding_background(row_id: str, message: str, conversation_id: str):
    """Genera embedding y actualiza la fila. Corre en thread separado."""
    try:
        emb = get_embedding(message)
        if emb:
            update_embedding(row_id, emb)
            log.info(
                f"[ELEVEN POSTCALL] embedding OK — "
                f"row_id={row_id} dims={len(emb)} "
                f"conversation_id={conversation_id!r}"
            )
        else:
            log.warning(
                f"[ELEVEN POSTCALL] embedding None — "
                f"row_id={row_id} conversation_id={conversation_id!r}"
            )
    except Exception as e:
        log.error(
            f"[ELEVEN POSTCALL] ERROR embedding row_id={row_id}: {e}",
            exc_info=True,
        )


@router.post("/post-call")
async def elevenlabs_post_call(request: Request):
    """
    Recibe post_call_transcription de ElevenLabs al finalizar una llamada de LIVI.
    Guarda cada mensaje del transcript en Livre Cloud con embedding automático.
    Responde rápido — el procesamiento de embeddings va en background.
    """
    # ── 1. Parsear body ──────────────────────────────────────────────────────
    try:
        body = await request.json()
    except Exception as e:
        log.error(f"[ELEVEN POSTCALL] ERROR parseando JSON: {e}")
        raise HTTPException(status_code=400, detail="JSON inválido")

    # ── 2. Extraer campos del payload ────────────────────────────────────────
    event_type      = body.get("type", "")
    event_id        = body.get("event_id", "")
    conversation_id = body.get("conversation_id", "")
    agent_id        = body.get("agent_id", "")
    status          = body.get("status", "")
    transcript      = body.get("transcript", [])
    metadata        = body.get("metadata", {})

    log.info(
        f"[ELEVEN POSTCALL] recibido — "
        f"type={event_type!r} "
        f"status={status!r} "
        f"conversation_id={conversation_id!r} "
        f"agent_id={agent_id!r} "
        f"mensajes={len(transcript)}"
    )

    # ── 3. Validaciones básicas ──────────────────────────────────────────────
    if not conversation_id:
        log.warning(f"[ELEVEN POSTCALL] sin conversation_id — body={str(body)[:200]}")
        return {"ok": True, "detail": "sin conversation_id, ignorado"}

    if not transcript:
        log.warning(f"[ELEVEN POSTCALL] transcript vacío — conversation_id={conversation_id!r}")
        return {"ok": True, "detail": "transcript vacío, ignorado"}

    log.info(
        f"[ELEVEN POSTCALL] conversation_id={conversation_id!r} "
        f"mensajes={len(transcript)} "
        f"duracion={metadata.get('call_duration_secs', '?')}s"
    )

    # ── 4. Idempotencia ──────────────────────────────────────────────────────
    if event_id and _already_processed(conversation_id, event_id):
        log.info(
            f"[ELEVEN POSTCALL] duplicado ignorado — "
            f"conversation_id={conversation_id!r} event_id={event_id!r}"
        )
        return {"ok": True, "detail": "ya procesado"}

    # ── 5. Crear usuario de sistema si no existe ─────────────────────────────
    if not user_exists(ELEVENLABS_SYSTEM_USER):
        create_user(ELEVENLABS_SYSTEM_USER)
        log.info(f"[ELEVEN POSTCALL] usuario creado — {ELEVENLABS_SYSTEM_USER}")

    # ── 6. Documento único por conversación ─────────────────────────────────
    doc_key  = f"livi_{conversation_id}"
    doc_name = f"Llamada LIVI {conversation_id[:12]}"
    doc_desc = (
        f"Transcripción de llamada LIVI — "
        f"conversation_id={conversation_id} "
        f"agent_id={agent_id} "
        f"duracion={metadata.get('call_duration_secs', '?')}s"
    )

    document_id, doc_created = get_or_create_document(
        ELEVENLABS_SYSTEM_USER, doc_key, doc_name, doc_desc
    )
    if doc_created:
        log.info(f"[ELEVEN POSTCALL] nuevo documento — key={doc_key!r}")

    # ── 7. Guardar cada mensaje del transcript ───────────────────────────────
    saved    = 0
    failed   = 0
    row_ids  = []

    for idx, turn in enumerate(transcript):
        role    = turn.get("role", "user")
        message = (turn.get("message") or "").strip()
        secs    = turn.get("time_in_call_secs", 0)

        # Normalizar role: ElevenLabs usa "agent", Livre usa "assistant"
        normalized_role = "assistant" if role == "agent" else "user"

        if not message:
            log.debug(f"[ELEVEN POSTCALL] turno {idx} vacío, saltando")
            continue

        log.info(
            f"[ELEVEN POSTCALL] turno {idx} | "
            f"role={normalized_role} | "
            f"secs={secs} | "
            f"chars={len(message)} | "
            f"preview={message[:80]!r}"
        )

        data = {
            "message":         message,
            "role":            normalized_role,
            "conversation_id": conversation_id,
            "agent_id":        agent_id,
            "event_id":        event_id,          # para idempotencia
            "time_in_call_secs": secs,
            "turn_index":      idx,
        }

        try:
            row_id, _ = add_row_without_embedding(document_id, data)
            row_ids.append((row_id, message))
            log.info(
                f"[ELEVEN POSTCALL] guardado — "
                f"row_id={row_id} turno={idx} role={normalized_role}"
            )
            saved += 1
        except Exception as e:
            log.error(
                f"[ELEVEN POSTCALL] ERROR guardando turno {idx}: {e}",
                exc_info=True,
            )
            failed += 1

    log.info(
        f"[ELEVEN POSTCALL] guardado — "
        f"conversation_id={conversation_id!r} "
        f"guardados={saved} fallidos={failed}"
    )

    # ── 8. Embeddings en background (no bloquea la respuesta) ───────────────
    def _run_embeddings():
        log.info(
            f"[ELEVEN POSTCALL] iniciando embeddings — "
            f"conversation_id={conversation_id!r} "
            f"filas={len(row_ids)}"
        )
        emb_ok = 0
        for row_id, message in row_ids:
            _generate_embedding_background(row_id, message, conversation_id)
            emb_ok += 1
        log.info(
            f"[ELEVEN POSTCALL] embeddings OK — "
            f"conversation_id={conversation_id!r} "
            f"procesados={emb_ok}/{len(row_ids)}"
        )

    threading.Thread(target=_run_embeddings, daemon=True).start()

    # ── 9. Respuesta inmediata a ElevenLabs ─────────────────────────────────
    return {
        "ok":             True,
        "conversation_id": conversation_id,
        "mensajes_guardados": saved,
        "mensajes_fallidos":  failed,
    }
