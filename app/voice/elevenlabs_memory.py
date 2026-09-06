"""
app/voice/elevenlabs_memory.py

Endpoint que recibe mensajes de ElevenLabs y los guarda en memoria de Livre Cloud
con embedding automático.

POST /elevenlabs/memory
Body: { "message": "...", "user_id": "..." }  ← user_id opcional
"""

import logging
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from app.memory.service import save_message_to_memory
from app.memory.repository import user_exists, create_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/elevenlabs", tags=["elevenlabs"])

# Usuario de sistema para mensajes de voz de ElevenLabs sin user_id explícito.
# Puede sobreescribirse enviando user_id en el body.
ELEVENLABS_SYSTEM_USER = "elevenlabs_voice"


class ElevenLabsMemoryRequest(BaseModel):
    message: str
    user_id: str | None = None   # opcional: si ElevenLabs lo envía
    role: str = "user"           # "user" o "assistant"


@router.post("/memory")
async def elevenlabs_memory(body: ElevenLabsMemoryRequest):
    """
    Recibe un mensaje de ElevenLabs, lo guarda en memoria y genera su embedding.

    Flujo:
      1. Recibir mensaje.
      2. Guardar inmediatamente en memory_rows (no bloquea ElevenLabs).
      3. Generar embedding en background.
      4. Devolver respuesta rápida.
    """
    message = body.message.strip() if body.message else ""
    user_id = (body.user_id or ELEVENLABS_SYSTEM_USER).strip()
    role = body.role if body.role in ("user", "assistant") else "user"

    log.info(f"[ELEVEN MEMORY] mensaje recibido — user={user_id} role={role} chars={len(message)}")

    if not message:
        log.warning("[ELEVEN MEMORY] mensaje vacío, ignorado")
        raise HTTPException(status_code=400, detail="El campo 'message' no puede estar vacío")

    # Crear usuario de sistema si no existe
    if not user_exists(user_id):
        create_user(user_id)
        log.info(f"[ELEVEN MEMORY] usuario creado — user_id={user_id}")

    # Guardar en memoria + embedding en background (no bloquea)
    try:
        save_message_to_memory(user_id, role, message)
        log.info(f"[ELEVEN MEMORY] guardado — user={user_id} role={role}")
        log.info(f"[ELEVEN MEMORY] embedding disparado en background — user={user_id}")
    except Exception as e:
        log.error(f"[ELEVEN MEMORY] ERROR al guardar: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error al guardar en memoria")

    log.info(f"[ELEVEN MEMORY] completo — respondiendo a ElevenLabs")

    return {
        "ok": True,
        "user_id": user_id,
        "role": role,
        "chars": len(message),
    }
