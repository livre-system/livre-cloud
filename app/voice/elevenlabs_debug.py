"""
app/voice/elevenlabs_debug.py

Endpoint temporal de debug para inspeccionar el payload real
que ElevenLabs envía en post_call_transcription.

NO guarda nada. NO genera embeddings. Solo loguea y responde 200.
"""

import json
import logging
from fastapi import APIRouter, Request

log = logging.getLogger(__name__)

router = APIRouter(prefix="/elevenlabs", tags=["elevenlabs-debug"])


@router.post("/post-call-debug")
async def post_call_debug(request: Request):
    """
    Recibe cualquier JSON de ElevenLabs y loguea el payload completo.
    Solo para inspección. No modifica nada.
    """
    # Headers relevantes
    headers = dict(request.headers)
    log.info(f"[ELEVEN DEBUG] === REQUEST RECIBIDO ===")
    log.info(f"[ELEVEN DEBUG] method={request.method} url={request.url}")
    log.info(f"[ELEVEN DEBUG] content-type={headers.get('content-type', 'N/A')}")
    log.info(f"[ELEVEN DEBUG] content-length={headers.get('content-length', 'N/A')}")
    log.info(f"[ELEVEN DEBUG] user-agent={headers.get('user-agent', 'N/A')}")
    log.info(f"[ELEVEN DEBUG] x-elevenlabs-signature={headers.get('x-elevenlabs-signature', 'N/A')}")
    log.info(f"[ELEVEN DEBUG] --- HEADERS COMPLETOS ---")
    for k, v in headers.items():
        log.info(f"[ELEVEN DEBUG] header: {k}={v!r}")

    # Body raw
    try:
        raw = await request.body()
        log.info(f"[ELEVEN DEBUG] --- BODY RAW ({len(raw)} bytes) ---")
        log.info(f"[ELEVEN DEBUG] raw={raw.decode('utf-8', errors='replace')}")
    except Exception as e:
        log.error(f"[ELEVEN DEBUG] ERROR leyendo body raw: {e}")
        raw = b""

    # Body parseado como JSON
    try:
        payload = json.loads(raw)
        log.info(f"[ELEVEN DEBUG] --- PAYLOAD JSON PARSEADO ---")
        log.info(f"[ELEVEN DEBUG] json={json.dumps(payload, indent=2, ensure_ascii=False)}")
        log.info(f"[ELEVEN DEBUG] keys_top_level={list(payload.keys())}")

        # Log de campos clave si existen
        for field in ["type", "event_id", "conversation_id", "agent_id", "status", "transcript"]:
            if field in payload:
                val = payload[field]
                if field == "transcript":
                    log.info(f"[ELEVEN DEBUG] transcript: {len(val)} turnos")
                    for i, turn in enumerate(val):
                        log.info(f"[ELEVEN DEBUG] turno {i}: {turn}")
                else:
                    log.info(f"[ELEVEN DEBUG] {field}={val!r}")

    except Exception as e:
        log.warning(f"[ELEVEN DEBUG] body no es JSON válido: {e}")

    log.info(f"[ELEVEN DEBUG] === FIN REQUEST ===")

    return {"ok": True, "debug": "payload logueado en Railway"}
