"""
app/voice/elevenlabs_debug.py

El webhook de ElevenLabs apunta a /elevenlabs/post-call-debug.
Este endpoint ahora hace el procesamiento completo: loguea Y guarda en memoria.
"""

import json
import logging
import threading
from fastapi import APIRouter, Request
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

router = APIRouter(prefix="/elevenlabs", tags=["elevenlabs-debug"])

ELEVENLABS_SYSTEM_USER = "elevenlabs_voice"


def _turn_already_saved(doc_key: str, turn_id: str) -> bool:
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
        log.warning(f"[ELEVEN POSTCALL] idempotencia check error: {e}")
        return False


def _embed_rows(rows_to_embed: list, conversation_id: str):
    ok = 0
    for row_id, message in rows_to_embed:
        try:
            emb = get_embedding(message)
            if emb:
                update_embedding(row_id, emb)
                log.info(f"[ELEVEN POSTCALL] embedding OK — row_id={row_id} dims={len(emb)}")
                ok += 1
            else:
                log.warning(f"[ELEVEN POSTCALL] embedding None — row_id={row_id}")
        except Exception as e:
            log.error(f"[ELEVEN POSTCALL] ERROR embedding row_id={row_id}: {e}")
    log.info(f"[ELEVEN POSTCALL] embeddings OK — conversation_id={conversation_id!r} procesados={ok}/{len(rows_to_embed)}")


@router.post("/post-call-debug")
async def post_call_debug(request: Request):
    """
    El webhook de ElevenLabs apunta aquí.
    Loguea el payload completo Y procesa el transcript guardando en memoria.
    """
    # ── Leer body ────────────────────────────────────────────────────────────
    try:
        raw = await request.body()
    except Exception as e:
        log.error(f"[ELEVEN POSTCALL] ERROR leyendo body: {e}")
        return {"ok": False}

    log.info(f"[ELEVEN POSTCALL] request recibido — {len(raw)} bytes")
    log.info(f"[ELEVEN POSTCALL] content-type={request.headers.get('content-type', 'N/A')}")
    log.info(f"[ELEVEN POSTCALL] signature={request.headers.get('elevenlabs-signature', 'N/A')[:40]}")

    # ── Parsear JSON ─────────────────────────────────────────────────────────
    try:
        body = json.loads(raw)
    except Exception as e:
        log.error(f"[ELEVEN POSTCALL] ERROR parseando JSON: {e}")
        return {"ok": False}

    # ── Extraer campos ───────────────────────────────────────────────────────
    event_type      = body.get("type", "")
    data            = body.get("data", {}) or {}
    conversation_id = body.get("conversation_id") or data.get("conversation_id") or ""
    agent_id        = data.get("agent_id", "")
    whatsapp_user   = data.get("user_id", "")
    status          = data.get("status", "")
    metadata        = data.get("metadata", {}) or {}
    duration_secs   = metadata.get("call_duration_secs", 0)
    transcript      = body.get("transcript", []) or []

    log.info(
        f"[ELEVEN POSTCALL] recibido — "
        f"type={event_type!r} status={status!r} "
        f"conversation_id={conversation_id!r} "
        f"whatsapp_user={whatsapp_user!r} "
        f"mensajes={len(transcript)} duracion={duration_secs}s"
    )

    # ── Validaciones ─────────────────────────────────────────────────────────
    if not conversation_id:
        log.warning("[ELEVEN POSTCALL] sin conversation_id — ignorado")
        return {"ok": True, "detail": "sin conversation_id"}

    if not transcript:
        log.warning(f"[ELEVEN POSTCALL] transcript vacío — conversation_id={conversation_id!r}")
        return {"ok": True, "detail": "transcript vacío"}

    log.info(f"[ELEVEN POSTCALL] conversation_id={conversation_id!r}")
    log.info(f"[ELEVEN POSTCALL] mensajes={len(transcript)} duracion={duration_secs}s")

    # ── Crear usuario de sistema ─────────────────────────────────────────────
    if not user_exists(ELEVENLABS_SYSTEM_USER):
        create_user(ELEVENLABS_SYSTEM_USER)
        log.info(f"[ELEVEN POSTCALL] usuario creado — {ELEVENLABS_SYSTEM_USER}")

    # ── Documento por conversación ───────────────────────────────────────────
    doc_key  = f"livi_{conversation_id}"
    doc_name = f"LIVI {conversation_id[:16]}"
    doc_desc = (
        f"Conversación LIVI — conversation_id={conversation_id} "
        f"whatsapp={whatsapp_user} duracion={duration_secs}s"
    )

    document_id, doc_created = get_or_create_document(
        ELEVENLABS_SYSTEM_USER, doc_key, doc_name, doc_desc
    )
    if doc_created:
        log.info(f"[ELEVEN POSTCALL] nuevo documento — key={doc_key!r}")

    # ── Guardar turnos ───────────────────────────────────────────────────────
    saved = skipped = failed = 0
    rows_to_embed = []

    for idx, turn in enumerate(transcript):
        role    = turn.get("role", "user")
        message = (turn.get("message") or "").strip()
        secs    = turn.get("time_in_call_secs", 0)
        turn_id = turn.get("id", "")

        if not message:
            skipped += 1
            continue

        normalized_role = "assistant" if role == "agent" else "user"

        if turn_id and _turn_already_saved(doc_key, turn_id):
            log.info(f"[ELEVEN POSTCALL] turno {idx} ya existe — turn_id={turn_id!r}")
            skipped += 1
            continue

        log.info(
            f"[ELEVEN POSTCALL] turno {idx} | role={normalized_role} | "
            f"secs={secs} | chars={len(message)} | preview={message[:80]!r}"
        )

        data_row = {
            "message":           message,
            "role":              normalized_role,
            "conversation_id":   conversation_id,
            "agent_id":          agent_id,
            "whatsapp_user":     whatsapp_user,
            "turn_id":           turn_id,
            "time_in_call_secs": secs,
            "turn_index":        idx,
        }

        try:
            row_id, _ = add_row_without_embedding(document_id, data_row)
            rows_to_embed.append((row_id, message))
            log.info(f"[ELEVEN POSTCALL] guardado — row_id={row_id} turno={idx} role={normalized_role}")
            saved += 1
        except Exception as e:
            log.error(f"[ELEVEN POSTCALL] ERROR guardando turno {idx}: {e}", exc_info=True)
            failed += 1

    log.info(
        f"[ELEVEN POSTCALL] guardado — "
        f"conversation_id={conversation_id!r} "
        f"guardados={saved} saltados={skipped} fallidos={failed}"
    )

    # ── Embeddings en background ─────────────────────────────────────────────
    if rows_to_embed:
        threading.Thread(
            target=_embed_rows,
            args=(rows_to_embed, conversation_id),
            daemon=True,
        ).start()
        log.info(f"[ELEVEN POSTCALL] embeddings iniciados — filas={len(rows_to_embed)}")

    return {
        "ok":              True,
        "conversation_id": conversation_id,
        "guardados":       saved,
        "saltados":        skipped,
        "fallidos":        failed,
    }
