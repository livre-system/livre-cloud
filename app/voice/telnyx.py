"""
app/voice/telnyx.py

Integración Telnyx Media Streams + ElevenLabs Conversational AI.

Arquitectura:
  Telnyx → WebSocket → backend → ElevenLabs → WebSocket → backend → Telnyx

Audio flow verificado contra documentación oficial:
  - Telnyx INBOUND:  base64-encoded RTP payload SIN cabecera (doc: "no headers")
                     Codec = PCMU 8kHz
  - Telnyx OUTBOUND: JSON { event:"media", media:{ payload: base64(RTP) } }
                     RTP con cabecera, codec PCMU 8kHz
  - ElevenLabs manda/recibe PCM 16-bit 16kHz en base64

Llamadas salientes:
  Call Control API: POST /v2/calls con stream_url + stream_bidirectional_mode=rtp
  El streaming arranca automáticamente al contestar. Sin TeXML.

Llamadas entrantes:
  Telnyx llama al webhook /incoming → respondemos TeXML <Connect><Stream>
  Esto es obligatorio para llamadas entrantes en Telnyx.
"""

import os
import re
import json
import base64
import struct
import audioop
import logging
import asyncio
import httpx
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.voice.address_matching import (
    search_top3_streets,
    is_yes,
    is_no,
    normalize_numbers_in_text,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice-telnyx", tags=["voice-telnyx"])

# ---------------------------------------------------------------------------
# Variables de entorno
# ---------------------------------------------------------------------------
TELNYX_API_KEY       = os.getenv("TELNYX_API_KEY", "")
TELNYX_CONNECTION_ID = os.getenv("TELNYX_CONNECTION_ID", "")  # Call Control App ID
TELNYX_PHONE_NUMBER  = os.getenv("TELNYX_PHONE_NUMBER", "")
BASE_URL             = os.getenv("BASE_URL", "")

# ElevenLabs — mismos valores que elevenlabs.py, no se tocan
ELEVENLABS_API_KEY   = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID  = "agent_3701m0dfvnmmemptqcm2jym9wdvv"
ELEVENLABS_VOICE_ID  = os.getenv("ELEVENLABS_VOICE_ID", "2KrcT8wE5IyKnWz2nknz")

# ---------------------------------------------------------------------------
# Audio helpers
#
# Documentación oficial confirmada:
# https://developers.telnyx.com/docs/voice/programmable-voice/media-streaming
#
#   RECIBIR de Telnyx (inbound del teléfono):
#     "The payload contains a base64-encoded RTP payload (no headers)."
#     → base64decode directo. Ya es audio PCMU 8kHz puro, sin cabecera RTP.
#     CORRECCIÓN respecto a versión anterior: NO hay que stripear 12 bytes.
#
#   ENVIAR a Telnyx (bidirectional rtp mode):
#     "your base64 encoded RTP stream"
#     → hay que armar paquete RTP completo con cabecera (12 bytes) + payload.
# ---------------------------------------------------------------------------

_rtp_seq       = 0
_rtp_timestamp = 0
_rtp_ssrc      = 0xCAFEBABE


def _pcmu8k_to_pcm16k_b64(pcmu_bytes: bytes) -> str | None:
    """
    PCMU 8kHz (ulaw, sin cabecera) → PCM 16-bit little-endian 16kHz → base64.
    Esto es lo que ElevenLabs espera como user_audio_chunk.
    """
    try:
        pcm8k  = audioop.ulaw2lin(pcmu_bytes, 2)
        pcm16k, _ = audioop.ratecv(pcm8k, 2, 1, 8000, 16000, None)
        return base64.b64encode(pcm16k).decode()
    except Exception as e:
        logger.error(f"[TELNYX] Error PCMU→PCM16k: {e}")
        return None


def _build_rtp_pcmu(pcm16k_bytes: bytes) -> str:
    """
    PCM 16kHz little-endian (de ElevenLabs) → PCMU 8kHz → paquete RTP → base64.
    Telnyx espera RTP completo (con cabecera) en modo bidirectional rtp.
    PT=0 = PCMU según RFC 3551.
    """
    global _rtp_seq, _rtp_timestamp

    # Convertir PCM16k → PCMU 8kHz
    pcm8k, _ = audioop.ratecv(pcm16k_bytes, 2, 1, 16000, 8000, None)
    pcmu     = audioop.lin2ulaw(pcm8k, 2)

    _rtp_seq       = (_rtp_seq + 1) & 0xFFFF
    _rtp_timestamp = (_rtp_timestamp + len(pcmu)) & 0xFFFFFFFF

    # Cabecera RTP mínima 12 bytes
    # V=2, P=0, X=0, CC=0 → 0x80
    # M=0, PT=0 (PCMU)    → 0x00
    header = struct.pack(
        "!BBHII",
        0x80,
        0x00,           # PT=0 = PCMU
        _rtp_seq,
        _rtp_timestamp,
        _rtp_ssrc,
    )
    return base64.b64encode(header + pcmu).decode()


# ---------------------------------------------------------------------------
# TeXML para llamadas ENTRANTES
# ---------------------------------------------------------------------------

def _build_incoming_texml(ws_url: str) -> str:
    """
    TeXML mínimo para llamadas entrantes.
    Abre el WebSocket bidireccional con PCMU 8kHz en ambas direcciones.
    bidirectionalMode=rtp es obligatorio para audio en tiempo real.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream
      url="{ws_url}"
      track="both_tracks"
      bidirectionalMode="rtp"
      bidirectionalCodec="PCMU"
      bidirectionalSamplingRate="8000"
    />
  </Connect>
</Response>"""


# ---------------------------------------------------------------------------
# Validación de direcciones (calle + altura) sobre el transcript de usuario
#
# Reutiliza la búsqueda por embedding de app/voice/address_matching.py (la
# misma que usa el flujo Twilio). No toca audio ni el WebSocket de Telnyx:
# solo lee el texto que ya llega en el evento "user_transcript" y le manda a
# ElevenLabs un "contextual_update" con la calle candidata para que el
# agente la confirme con el usuario en su propia voz.
#
# IMPORTANTE (ver aclaración al final de la respuesta): esto asume que el
# system prompt del agente de ElevenLabs sabe qué hacer con ese contexto
# (preguntar "¿Es <candidata>?"). El backend no puede forzar literalmente
# lo que el agente dice, solo puede informarle.
# ---------------------------------------------------------------------------

async def _send_context_to_eleven(eleven_ws, text: str):
    try:
        await eleven_ws.send(json.dumps({
            "type": "contextual_update",
            "text": text,
        }))
        logger.info(f"[TELNYX] dirección — contexto enviado a ElevenLabs: {text}")
    except Exception as e:
        logger.error(f"[TELNYX] Error enviando contextual_update: {e}")


async def _handle_user_transcript_for_address(text: str, eleven_ws, call_state: dict):
    """
    call_state es un dict propio de la llamada (vive en media_stream) con:
      - "pending": {"candidates": [...], "index": int, "number": str} | None
      - "confirmed_address": str | None
    """
    if not text:
        return

    pending = call_state.get("pending")

    try:
        if pending:
            candidates = pending["candidates"]
            index = pending["index"]
            number = pending["number"]

            if is_yes(text):
                direccion = f"{candidates[index]} {number}".strip()
                call_state["pending"] = None
                call_state["confirmed_address"] = direccion
                logger.info(f"[TELNYX] dirección confirmada: {direccion}")
                await _send_context_to_eleven(
                    eleven_ws, f"[DIRECCIÓN CONFIRMADA: {direccion}]"
                )
                return

            if is_no(text) and index + 1 < len(candidates):
                next_index = index + 1
                call_state["pending"]["index"] = next_index
                siguiente = f"{candidates[next_index]} {number}".strip()
                logger.info(f"[TELNYX] dirección — ofreciendo siguiente candidata: {siguiente}")
                await _send_context_to_eleven(
                    eleven_ws,
                    f"[CANDIDATA DE DIRECCIÓN A CONFIRMAR: {siguiente}] "
                    f"Preguntale al usuario, en una frase corta: ¿Es {siguiente}?",
                )
                return

            # Ni sí ni no reconocido, o se acabaron las candidatas: soltamos
            # la detección y dejamos que el agente siga la conversación normal.
            call_state["pending"] = None
            return

        # ---- Detección de una dirección nueva en el transcript ----
        normalized = normalize_numbers_in_text(text)
        numbers = re.findall(r'\b\d+\b', normalized)
        number = numbers[0] if numbers else ""

        # Si hay una calle pendiente de altura del turno anterior, combinamos.
        pending_street_candidates = call_state.get("pending_street")
        if number and pending_street_candidates:
            # Usuario dijo la altura después de haber dicho la calle sola.
            call_state["pending_street"] = None
            call_state["pending"] = {
                "candidates": pending_street_candidates,
                "index": 0,
                "number": number,
            }
            candidata = f"{pending_street_candidates[0]} {number}".strip()
            logger.info(f"[TELNYX] dirección — calle previa + altura nueva: {candidata}")
            await _send_context_to_eleven(
                eleven_ws,
                f"[CANDIDATA DE DIRECCIÓN A CONFIRMAR: {candidata}] "
                f"Preguntale al usuario, en una frase corta: ¿Es {candidata}?",
            )
            return

        candidates = await asyncio.to_thread(search_top3_streets, normalized)
        if not candidates:
            # Sin coincidencia razonable → el agente sigue y pide aclaración
            return

        if not number:
            # Calle detectada pero sin altura todavía: guardar y esperar.
            call_state["pending_street"] = candidates
            logger.info(
                f"[TELNYX] dirección — calle sin altura, guardando candidatas: {candidates}"
            )
            return

        call_state["pending"] = {"candidates": candidates, "index": 0, "number": number}
        call_state["pending_street"] = None
        candidata = f"{candidates[0]} {number}".strip()
        logger.info(f"[TELNYX] dirección — candidata detectada: {candidata}")
        await _send_context_to_eleven(
            eleven_ws,
            f"[CANDIDATA DE DIRECCIÓN A CONFIRMAR: {candidata}] "
            f"Preguntale al usuario, en una frase corta: ¿Es {candidata}?",
        )

    except Exception as e:
        # Cualquier falla acá jamás debe cortar la llamada ni el audio.
        logger.error(f"[TELNYX] Error validando dirección: {e}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/incoming")
async def incoming_call(request: Request):
    """
    Webhook para llamadas entrantes.
    Configurar en Telnyx: Portal → tu número → Voice URL → esta URL.
    """
    host   = BASE_URL.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{host}/voice-telnyx/stream"
    logger.info(f"[TELNYX] call/control — llamada entrante → {ws_url}")
    return Response(content=_build_incoming_texml(ws_url), media_type="application/xml")


@router.websocket("/stream")
async def media_stream(websocket: WebSocket):
    """
    WebSocket bidireccional Telnyx ↔ ElevenLabs.

    INBOUND (teléfono → ElevenLabs):
      Telnyx manda: { event:"media", media:{ track:"inbound", payload:"<b64 PCMU 8kHz sin RTP header>" } }
      → decode b64 → PCMU bytes → convertir a PCM 16kHz → ElevenLabs

    OUTBOUND (ElevenLabs → teléfono):
      ElevenLabs manda: PCM 16kHz little-endian en base64
      → decodificar → convertir a PCMU 8kHz → armar RTP con cabecera → Telnyx
    """
    await websocket.accept()
    logger.info("[TELNYX] conexión — WebSocket aceptado")

    eleven_ws    = None
    eleven_ready = asyncio.Event()
    stop_event   = asyncio.Event()   # señaliza cierre a ambos loops
    call_state: dict = {"pending": None, "confirmed_address": None, "pending_street": None}

    async def _close_eleven(reason: str = "stop"):
        """Envía close_socket a ElevenLabs y cierra el WebSocket limpiamente."""
        ws = eleven_ws
        if ws is None:
            return
        logger.info(f"[ELEVEN] cerrando conversación (razón: {reason})")
        try:
            if ws.open:
                await ws.send(json.dumps({"type": "close_socket"}))
        except Exception:
            pass  # ya estaba cerrado, ignorar
        try:
            await ws.close()
        except Exception:
            pass
        logger.info("[ELEVEN] WebSocket cerrado")

    try:
        # Conectar a ElevenLabs
        eleven_ws = await websockets.connect(
            f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={ELEVENLABS_AGENT_ID}",
            additional_headers={"xi-api-key": ELEVENLABS_API_KEY},
        )
        logger.info("[ELEVEN] conexión — WebSocket conectado")

        # Configuración de inicio de conversación.
        # voice_id en tts override: fuerza la voz aunque el agente tenga otra
        # configurada en el dashboard. Fuente: documentación oficial ElevenLabs
        # https://elevenlabs.io/docs/eleven-agents/api-reference/eleven-agents/websocket
        initiation_payload: dict = {
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {
                "agent": {"language": "es"},
                "tts":   {"optimize_streaming_latency": 3},
            },
        }
        if ELEVENLABS_VOICE_ID:
            initiation_payload["conversation_config_override"]["tts"]["voice_id"] = ELEVENLABS_VOICE_ID
            logger.info(f"[ELEVEN] voz override activo — voice_id={ELEVENLABS_VOICE_ID}")
        else:
            logger.warning("[ELEVEN] ELEVENLABS_VOICE_ID no configurado — se usará la voz del agente en el dashboard")
        await eleven_ws.send(json.dumps(initiation_payload))

        # ------------------------------------------------------------------
        # LOOP 1: Telnyx → ElevenLabs
        # ------------------------------------------------------------------
        async def telnyx_to_eleven():
            audio_chunks = 0
            try:
                async for raw_msg in websocket.iter_text():
                    data  = json.loads(raw_msg)
                    event = data.get("event")

                    if event == "connected":
                        logger.info("[TELNYX] call/control — connected")

                    elif event == "start":
                        fmt    = data.get("start", {}).get("media_format", {})
                        caller = data.get("start", {}).get("from", "?")
                        sid    = data.get("stream_id", "?")
                        logger.info(f"[TELNYX] call/control — start stream_id={sid} from={caller} format={fmt}")

                    elif event == "media":
                        await eleven_ready.wait()

                        track       = data.get("media", {}).get("track", "inbound")
                        payload_b64 = data.get("media", {}).get("payload", "")

                        # Ignorar outbound (es lo que nosotros mandamos)
                        if track == "outbound" or not payload_b64:
                            continue

                        audio_chunks += 1
                        if audio_chunks % 100 == 0:
                            logger.info(f"[TELNYX] audio recibido — {audio_chunks} chunks")

                        # Payload = PCMU 8kHz puro, SIN cabecera RTP (confirmado en doc)
                        try:
                            pcmu_bytes = base64.b64decode(payload_b64)
                        except Exception as e:
                            logger.error(f"[TELNYX] Error decode payload: {e}")
                            continue

                        # PCMU 8kHz → PCM 16kHz → ElevenLabs
                        pcm16k_b64 = _pcmu8k_to_pcm16k_b64(pcmu_bytes)
                        if pcm16k_b64 is None:
                            continue

                        try:
                            await eleven_ws.send(json.dumps({"user_audio_chunk": pcm16k_b64}))
                            if audio_chunks % 100 == 0:
                                logger.info(f"[ELEVEN] audio enviado — {audio_chunks} chunks")
                        except Exception as e:
                            logger.error(f"[TELNYX→ELEVEN] Error: {e}")
                            break

                    elif event == "stop":
                        logger.info(f"[TELNYX] stop recibido — total audio: {audio_chunks} chunks")
                        stop_event.set()
                        await _close_eleven(reason="telnyx-stop")
                        break

                    elif event == "dtmf":
                        logger.info(f"[TELNYX] call/control — DTMF: {data.get('dtmf', {}).get('digit')}")

                    elif event == "error":
                        logger.error(f"[TELNYX] call/control — error: {data}")

                    else:
                        logger.debug(f"[TELNYX] call/control — evento: {event}")

            except WebSocketDisconnect:
                logger.info("[TELNYX] conexión — desconectado (disconnect abrupto)")
                stop_event.set()
                await _close_eleven(reason="telnyx-disconnect")

        # ------------------------------------------------------------------
        # LOOP 2: ElevenLabs → Telnyx
        # ------------------------------------------------------------------
        async def eleven_to_telnyx():
            try:
                async for raw_msg in eleven_ws:
                    # Si ya se señalizó el cierre, descartar mensajes restantes
                    # y dejar que el loop termine naturalmente.
                    if stop_event.is_set():
                        break

                    data       = json.loads(raw_msg)
                    event_type = data.get("type")

                    if event_type == "conversation_initiation_metadata":
                        meta    = data.get("conversation_initiation_metadata_event", {})
                        conv_id = meta.get("conversation_id", "?")
                        out_fmt = meta.get("agent_output_audio_format", "?")
                        in_fmt  = meta.get("user_input_audio_format", "?")
                        logger.info(f"[ELEVEN] conexión — sesión iniciada id={conv_id} entrada={in_fmt} salida={out_fmt}")
                        eleven_ready.set()

                    elif event_type == "audio":
                        audio_b64 = data.get("audio_event", {}).get("audio_base_64", "")
                        if not audio_b64:
                            continue
                        try:
                            pcm16k_bytes = base64.b64decode(audio_b64)
                            rtp_b64      = _build_rtp_pcmu(pcm16k_bytes)
                            await websocket.send_text(json.dumps({
                                "event": "media",
                                "media": {"payload": rtp_b64},
                            }))
                            logger.debug("[TELNYX] audio enviado — chunk PCMU RTP")
                        except Exception as e:
                            logger.error(f"[ELEVEN→TELNYX] Error: {e}")

                    elif event_type == "interruption":
                        logger.info("[ELEVEN] audio enviado — interrupción, clear buffer")
                        try:
                            await websocket.send_text(json.dumps({"event": "clear"}))
                        except Exception:
                            pass

                    elif event_type == "ping":
                        event_id = data.get("ping_event", {}).get("event_id")
                        if event_id:
                            await eleven_ws.send(json.dumps({"type": "pong", "event_id": event_id}))

                    elif event_type == "agent_response":
                        text = data.get("agent_response_event", {}).get("agent_response", "")
                        logger.info(f"[ELEVEN GPT] '{text}'")

                    elif event_type == "user_transcript":
                        text = data.get("user_transcription_event", {}).get("user_transcript", "")
                        logger.info(f"[ELEVEN USUARIO] '{text}'")
                        await _handle_user_transcript_for_address(text, eleven_ws, call_state)

                    elif event_type == "error":
                        logger.error(f"[ELEVEN ERROR] {data}")

                    else:
                        logger.debug(f"[ELEVEN] evento: {event_type}")

            except Exception as e:
                if not stop_event.is_set():
                    # Solo loguear si NO fue un cierre intencional nuestro
                    logger.error(f"[ELEVEN] eleven_to_telnyx error: {e}")

        await asyncio.gather(telnyx_to_eleven(), eleven_to_telnyx())

    except Exception as e:
        logger.exception(f"[TELNYX] Error en media_stream: {e}")
    finally:
        # Garantía final: si por alguna razón _close_eleven no se llamó antes
        # (excepción inesperada en gather), cerramos igual.
        if not stop_event.is_set():
            await _close_eleven(reason="finally-fallback")
        logger.info("[TELNYX] conexión — sesión finalizada")


# ---------------------------------------------------------------------------
# Llamada SALIENTE — Call Control API, sin TeXML
#
# Doc: https://developers.telnyx.com/api/call-control/dial-call
# Endpoint: POST https://api.telnyx.com/v2/calls
#
# stream_url + stream_bidirectional_mode=rtp arranca el WebSocket
# automáticamente al contestar la llamada. No hace falta webhook adicional.
# ---------------------------------------------------------------------------

class OutboundCallRequest(BaseModel):
    to: str


@router.post("/call")
async def make_call(body: OutboundCallRequest):
    """
    Llamada saliente via Call Control API de Telnyx.

    Variables requeridas en Railway:
      TELNYX_API_KEY       → KEY... (Portal → Auth → API Keys)
      TELNYX_CONNECTION_ID → ID de la Call Control App (no TeXML App)
      TELNYX_PHONE_NUMBER  → número Telnyx E.164 (+1...)
      BASE_URL             → https://livre-cloud-production.up.railway.app
    """
    if not TELNYX_API_KEY or not TELNYX_CONNECTION_ID or not TELNYX_PHONE_NUMBER:
        raise HTTPException(
            status_code=500,
            detail="Faltan: TELNYX_API_KEY, TELNYX_CONNECTION_ID o TELNYX_PHONE_NUMBER.",
        )

    host   = BASE_URL.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{host}/voice-telnyx/stream"

    payload = {
        "to":            body.to,
        "from":          TELNYX_PHONE_NUMBER,
        "connection_id": TELNYX_CONNECTION_ID,
        # Streaming bidireccional arranca al contestar
        "stream_url":                         ws_url,
        "stream_track":                       "both_tracks",
        "stream_bidirectional_mode":          "rtp",
        "stream_bidirectional_codec":         "PCMU",
        "stream_bidirectional_sampling_rate": 8000,
    }

    logger.info(f"[TELNYX] call/control — llamada saliente to={body.to} stream={ws_url}")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.telnyx.com/v2/calls",
                headers={
                    "Authorization": f"Bearer {TELNYX_API_KEY}",
                    "Content-Type":  "application/json",
                    "Accept":        "application/json",
                },
                json=payload,
            )

        if resp.status_code not in (200, 201, 202):
            logger.error(f"[TELNYX] Error saliente {resp.status_code}: {resp.text[:400]}")
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Telnyx {resp.status_code}: {resp.text[:400]}",
            )

        data            = resp.json().get("data", {})
        call_control_id = data.get("call_control_id", "?")
        logger.info(f"[TELNYX] call/control — iniciada call_control_id={call_control_id}")

        return {
            "call_control_id": call_control_id,
            "call_leg_id":     data.get("call_leg_id"),
            "to":              body.to,
            "from":            TELNYX_PHONE_NUMBER,
            "stream_url":      ws_url,
        }

    except httpx.RequestError as e:
        logger.exception(f"[TELNYX] Error de red: {e}")
        raise HTTPException(status_code=502, detail=f"Error de red: {e}")
