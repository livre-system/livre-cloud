"""
app/voice/elevenlabs_memory.py

Endpoint que recibe el Webhook Tool de ElevenLabs (guardar_memoria)
y guarda el mensaje en memoria de Livre Cloud con embedding automático.

Payload real de ElevenLabs Webhook Tool:
{
  "tool_call_id": "...",
  "tool_name":    "guardar_memoria",
  "parameters": {
    "message": "..."
  },
  "conversation_id": "..."
}

ElevenLabs espera como respuesta un JSON con "result" (string).
"""

import logging
from fastapi import APIRouter, Request, HTTPException
from app.memory.service import save_message_to_memory
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


@router.post("/memory")
async def elevenlabs_memory(request: Request):
    """
    Recibe el Webhook Tool 'guardar_memoria' de ElevenLabs.
    Lee parameters.message y conversation_id del payload real.
    Guarda en memoria + embedding. Responde rápido para no bloquear LIVI.
    Es idempotente: si el mismo tool_call_id ya fue procesado, devuelve OK sin duplicar.
    """
    # ── Parsear body raw (ElevenLabs puede mandar content-type variado) ──
    try:
        body = await request.json()
    except Exception as e:
        log.error(f"[ELEVEN MEMORY] ERROR parseando JSON: {e}")
        raise HTTPException(status_code=400, detail="JSON inválido")

    # ── Extraer campos del payload real de ElevenLabs ──
    tool_call_id    = body.get("tool_call_id", "")
    tool_name       = body.get("tool_name", "")
    parameters      = body.get("parameters", {})
    conversation_id = body.get("conversation_id", "")

    # parameters.message es el campo configurado en el Webhook Tool
    message = ""
    if isinstance(parameters, dict):
        message = (parameters.get("message") or "").strip()

    # Fallback: si ElevenLabs manda "message" directo en el body (versiones futuras)
    if not message:
        message = (body.get("message") or "").strip()

    log.info(
        f"[ELEVEN MEMORY] payload recibido — "
        f"tool_name={tool_name!r} "
        f"tool_call_id={tool_call_id!r} "
        f"conversation_id={conversation_id!r}"
    )

    if not message:
        log.warning(f"[ELEVEN MEMORY] mensaje vacío — body={body}")
        # Devolver OK igual para no romper el flujo de LIVI
        return {"result": "ok, mensaje vacío ignorado"}

    log.info(
        f"[ELEVEN MEMORY] mensaje recibido — "
        f"conversation_id={conversation_id!r} "
        f"chars={len(message)} "
        f"preview={message[:100]!r}"
    )

    # ── Idempotencia: verificar si este tool_call_id ya fue procesado ──
    if tool_call_id:
        try:
            with get_cursor() as cur:
                cur.execute("""
                    SELECT mr.id FROM memory_rows mr
                    JOIN memory_documents md ON md.id = mr.document_id
                    WHERE md.user_id = %s
                      AND mr.data->>'tool_call_id' = %s
                    LIMIT 1
                """, [ELEVENLABS_SYSTEM_USER, tool_call_id])
                existing = cur.fetchone()
            if existing:
                log.info(f"[ELEVEN MEMORY] duplicado ignorado — tool_call_id={tool_call_id!r}")
                return {"result": "ok, ya procesado"}
        except Exception as e:
            log.warning(f"[ELEVEN MEMORY] no se pudo verificar idempotencia: {e}")

    # ── Crear usuario de sistema si no existe ──
    if not user_exists(ELEVENLABS_SYSTEM_USER):
        create_user(ELEVENLABS_SYSTEM_USER)
        log.info(f"[ELEVEN MEMORY] usuario creado — {ELEVENLABS_SYSTEM_USER}")

    # ── Documento por conversation_id (cada llamada = un documento) ──
    doc_key  = f"livi_{conversation_id}" if conversation_id else "livi_general"
    doc_name = f"Llamada LIVI {conversation_id}" if conversation_id else "Llamada LIVI"
    doc_desc = f"Conversación de LIVI — conversation_id={conversation_id}"

    document_id, doc_created = get_or_create_document(
        ELEVENLABS_SYSTEM_USER, doc_key, doc_name, doc_desc
    )
    if doc_created:
        log.info(f"[ELEVEN MEMORY] nuevo documento — key={doc_key!r}")

    # ── Guardar fila con metadata (sin embedding primero — no bloquea LIVI) ──
    data = {
        "message":         message,
        "role":            "assistant",   # LIVI es el asistente
        "conversation_id": conversation_id,
        "tool_call_id":    tool_call_id,
        "tool_name":       tool_name,
    }

    try:
        row_id, _ = add_row_without_embedding(document_id, data)
        log.info(f"[ELEVEN MEMORY] guardado — row_id={row_id} doc={doc_key!r}")
    except Exception as e:
        log.error(f"[ELEVEN MEMORY] ERROR al guardar fila: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error al guardar en memoria")

    # ── Generar embedding en background (no bloquea la respuesta a ElevenLabs) ──
    import asyncio
    import threading

    def _gen_embedding():
        try:
            emb = get_embedding(message)
            if emb:
                update_embedding(row_id, emb)
                log.info(
                    f"[ELEVEN MEMORY] embedding OK — "
                    f"row_id={row_id} dims={len(emb)} "
                    f"conversation_id={conversation_id!r}"
                )
            else:
                log.warning(f"[ELEVEN MEMORY] embedding None — row_id={row_id}")
        except Exception as e:
            log.error(f"[ELEVEN MEMORY] ERROR embedding: {e}", exc_info=True)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.run_in_executor(None, _gen_embedding)
        else:
            threading.Thread(target=_gen_embedding, daemon=True).start()
    except Exception:
        threading.Thread(target=_gen_embedding, daemon=True).start()

    log.info(
        f"[ELEVEN MEMORY] completo — "
        f"conversation_id={conversation_id!r} "
        f"row_id={row_id}"
    )

    # ElevenLabs requiere { "result": "..." } como respuesta del Webhook Tool
    return {"result": f"Memoria guardada correctamente. conversation_id={conversation_id}"}
