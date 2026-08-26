"""
app/voice/telnyx.py

Integración Telnyx Media Streams + ElevenLabs Conversational AI.
Telnyx manda audio por WebSocket → ElevenLabs lo procesa → responde con voz.

Diferencias clave respecto a Twilio:
  - Telnyx manda paquetes RTP completos (12 bytes de cabecera + payload PCMU 8kHz)
  - Para enviar audio de vuelta también se mandan paquetes RTP base64
  - El TeXML usa <Connect><Stream bidirectionalMode="rtp"> en lugar de <Connect><Stream>
  - Las llamadas salientes van por REST a /v2/texml/calls/:application_id

Endpoints:
  POST /voice-telnyx/incoming  → webhook Telnyx para llamadas entrantes
  WS   /voice-telnyx/stream    → Media Stream WebSocket
  POST /voice-telnyx/call      → llamada saliente
"""

import os
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice-telnyx", tags=["voice-telnyx"])

# ---------------------------------------------------------------------------
# Variables de entorno
# ---------------------------------------------------------------------------
TELNYX_API_KEY          = os.getenv("TELNYX_API_KEY", "")
TELNYX_CONNECTION_ID    = os.getenv("TELNYX_CONNECTION_ID", "")   # application_id para llamadas salientes
TELNYX_PHONE_NUMBER     = os.getenv("TELNYX_PHONE_NUMBER", "")
BASE_URL                = os.getenv("BASE_URL", "")

# ElevenLabs — reutilizamos exactamente los mismos valores que elevenlabs.py
ELEVENLABS_API_KEY      = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID     = "agent_3701m0dfvnmmemptqcm2jym9wdvv"   # mismo agent ID
ELEVENLABS_VOICE_ID     = os.getenv("ELEVENLABS_VOICE_ID", "2KrcT8wE5IyKnWz2nknz")

# ---------------------------------------------------------------------------
# RTP helpers
#
# Telnyx manda paquetes RTP COMPLETOS (con cabecera de 12 bytes) codificados
# en base64. Para ElevenLabs necesitamos PCM 16-bit 16kHz sin cabecera.
# Para enviar de vuelta también necesitamos armar un paquete RTP válido.
# ---------------------------------------------------------------------------

RTP_HEADER_SIZE = 12   # cabecera RTP fija (sin extensiones ni CSRC)

# Contador de secuencia y timestamp globales para los paquetes que enviamos
_rtp_seq      = 0
_rtp_timestamp = 0
_rtp_ssrc     = 0xDEADBEEF   # identificador de fuente arbitrario


def _strip_rtp_header(rtp_b64: str) -> bytes | None:
    """
    Recibe un paquete RTP completo en base64.
    Devuelve solo el payload (PCMU 8kHz) sin cabecera.
    Devuelve None si el paquete es demasiado corto.
    """
    try:
        raw = base64.b64decode(rtp_b64)
        if len(raw) <= RTP_HEADER_SIZE:
            return None
        return raw[RTP_HEADER_SIZE:]
    except Exception as e:
        logger.error(f"[TELNYX] Error decodificando RTP: {e}")
        return None


def _build_rtp_packet(payload: bytes) -> str:
    """
    Arma un paquete RTP mínimo con el payload dado y lo devuelve en base64.
    Usamos PT=0 (PCMU), que es lo que Telnyx espera con bidirectionalCodec=PCMU.
    """
    global _rtp_seq, _rtp_timestamp

    _rtp_seq       = (_rtp_seq + 1) & 0xFFFF
    _rtp_timestamp = (_rtp_timestamp + len(payload)) & 0xFFFFFFFF

    # Cabecera RTP:
    # V=2, P=0, X=0, CC=0 → primer byte = 0x80
    # M=0, PT=0 (PCMU)    → segundo byte = 0x00
    header = struct.pack(
        "!BBHII",
        0x80,           # versión 2, sin relleno, sin extensión, 0 fuentes
        0x00,           # sin marker, payload type 0 = PCMU
        _rtp_seq,
        _rtp_timestamp,
        _rtp_ssrc,
    )
    return base64.b64encode(header + payload).decode()


def _pcmu8k_to_pcm16k(pcmu_payload: bytes) -> bytes:
    """PCMU 8kHz (ulaw) → PCM 16-bit 16kHz, para ElevenLabs."""
    pcm8k  = audioop.ulaw2lin(pcmu_payload, 2)
    pcm16k, _ = audioop.ratecv(pcm8k, 2, 1, 8000, 16000, None)
    return pcm16k


def _pcm16k_to_pcmu8k(pcm16k: bytes) -> bytes:
    """PCM 16-bit 16kHz (ElevenLabs) → PCMU 8kHz, para Telnyx."""
    pcm8k, _ = audioop.ratecv(pcm16k, 2, 1, 16000, 8000, None)
    return audioop.lin2ulaw(pcm8k, 2)


# ---------------------------------------------------------------------------
# TeXML helpers
# ---------------------------------------------------------------------------

def _build_incoming_texml(ws_url: str) -> str:
    """
    TeXML para llamadas entrantes.
    <Connect><Stream bidirectionalMode="rtp"> conecta el audio en tiempo real
    al WebSocket y permite enviar audio de vuelta al mismo socket.
    bidirectionalSamplingRate="8000" porque ElevenLabs trabaja con 16kHz
    pero la conversión la hacemos nosotros; Telnyx solo necesita saber
    el formato de transporte (PCMU 8kHz).
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
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/incoming")
async def incoming_call(request: Request):
    """
    Webhook que Telnyx llama cuando llega una llamada al número configurado.
    Responde con TeXML que abre el WebSocket de media streaming.
    """
    host   = BASE_URL.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{host}/voice-telnyx/stream"
    logger.info(f"[TELNYX] Llamada entrante → stream en {ws_url}")
    texml  = _build_incoming_texml(ws_url)
    return Response(content=texml, media_type="application/xml")


@router.websocket("/stream")
async def media_stream(websocket: WebSocket):
    """
    WebSocket bidireccional entre Telnyx y ElevenLabs.

    Flujo:
      Telnyx manda RTP PCMU 8kHz → stripear cabecera → convertir a PCM 16kHz
        → mandar a ElevenLabs como user_audio_chunk

      ElevenLabs responde con PCM 16kHz base64 → convertir a PCMU 8kHz
        → armar paquete RTP → mandar a Telnyx como media event
    """
    await websocket.accept()
    logger.info("[TELNYX] WebSocket conectado")

    eleven_ws    = None
    stream_id    = None
    eleven_ready = asyncio.Event()

    try:
        # ---- Conectar a ElevenLabs ----------------------------------------
        eleven_ws = await websockets.connect(
            f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={ELEVENLABS_AGENT_ID}",
            additional_headers={"xi-api-key": ELEVENLABS_API_KEY},
        )
        logger.info("[ELEVEN] WebSocket conectado")

        # Inicialización del agente (mismo formato que elevenlabs.py)
        await eleven_ws.send(json.dumps({
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {
                "agent": {
                    "language": "es",
                },
                "tts": {
                    "optimize_streaming_latency": 3,
                },
            },
        }))
        logger.info("[ELEVEN] Initiation enviado")

        # ------------------------------------------------------------------
        # Telnyx → ElevenLabs
        # Recibimos paquetes RTP PCMU 8kHz, extraemos el payload, convertimos
        # a PCM 16kHz y lo mandamos a ElevenLabs.
        # ------------------------------------------------------------------
        async def telnyx_to_eleven():
            nonlocal stream_id
            audio_chunks = 0
            try:
                async for message in websocket.iter_text():
                    data  = json.loads(message)
                    event = data.get("event")

                    if event == "connected":
                        logger.info("[TELNYX] Evento: connected")

                    elif event == "start":
                        # El stream_id identifica este stream en particular
                        stream_id = data.get("stream_id")
                        media_fmt  = data.get("start", {}).get("media_format", {})
                        logger.info(f"[TELNYX] Evento: start — stream_id={stream_id}")
                        logger.info(f"[TELNYX] Formato audio: {media_fmt}")

                    elif event == "media":
                        await eleven_ready.wait()

                        payload_b64 = data.get("media", {}).get("payload", "")
                        if not payload_b64:
                            continue

                        track = data.get("media", {}).get("track", "inbound")
                        # Solo procesamos el audio del usuario (inbound)
                        if track == "outbound":
                            continue

                        audio_chunks += 1
                        if audio_chunks % 100 == 0:
                            logger.info(f"[TELNYX] audio recibido: {audio_chunks} chunks")

                        # Extraer payload del paquete RTP
                        pcmu_payload = _strip_rtp_header(payload_b64)
                        if pcmu_payload is None:
                            continue

                        try:
                            # PCMU 8kHz → PCM 16kHz → base64 para ElevenLabs
                            pcm16k    = _pcmu8k_to_pcm16k(pcmu_payload)
                            pcm16k_b64 = base64.b64encode(pcm16k).decode()

                            await eleven_ws.send(json.dumps({
                                "user_audio_chunk": pcm16k_b64,
                            }))
                            if audio_chunks % 100 == 0:
                                logger.info(f"[ELEVEN] audio enviado: {audio_chunks} chunks")

                        except Exception as e:
                            logger.error(f"[TELNYX→ELEVEN] Error convirtiendo audio: {e}")

                    elif event == "stop":
                        logger.info(f"[TELNYX] Evento: stop — total chunks: {audio_chunks}")
                        break

                    else:
                        logger.debug(f"[TELNYX] Evento desconocido: {event}")

            except WebSocketDisconnect:
                logger.info("[TELNYX] WebSocket desconectado")

        # ------------------------------------------------------------------
        # ElevenLabs → Telnyx
        # Recibimos PCM 16kHz de ElevenLabs, convertimos a PCMU 8kHz,
        # armamos un paquete RTP y lo mandamos a Telnyx.
        # ------------------------------------------------------------------
        async def eleven_to_telnyx():
            try:
                async for message in eleven_ws:
                    data       = json.loads(message)
                    event_type = data.get("type")

                    if event_type == "conversation_initiation_metadata":
                        meta      = data.get("conversation_initiation_metadata_event", {})
                        conv_id   = meta.get("conversation_id", "?")
                        out_fmt   = meta.get("agent_output_audio_format", "?")
                        in_fmt    = meta.get("user_input_audio_format", "?")
                        logger.info(f"[ELEVEN] Sesión iniciada — conversation_id={conv_id}")
                        logger.info(f"[ELEVEN] audio entrada={in_fmt} | salida={out_fmt}")
                        eleven_ready.set()

                    elif event_type == "audio":
                        audio_b64 = data.get("audio_event", {}).get("audio_base_64", "")
                        if audio_b64:
                            try:
                                # PCM 16kHz → PCMU 8kHz → paquete RTP → base64
                                pcm16k      = base64.b64decode(audio_b64)
                                pcmu        = _pcm16k_to_pcmu8k(pcm16k)
                                rtp_b64     = _build_rtp_packet(pcmu)

                                await websocket.send_text(json.dumps({
                                    "event": "media",
                                    "media": {
                                        "payload": rtp_b64,
                                    },
                                }))
                                logger.debug("[TELNYX] audio enviado")

                            except Exception as e:
                                logger.error(f"[ELEVEN→TELNYX] Error convirtiendo audio: {e}")

                    elif event_type == "interruption":
                        # Telnyx no tiene comando "clear" por WebSocket como Twilio.
                        # Lo que se puede hacer es simplemente dejar de mandar audio.
                        logger.info("[ELEVEN] Interrupción detectada — deteniendo envío")

                    elif event_type == "ping":
                        event_id = data.get("ping_event", {}).get("event_id")
                        if event_id:
                            await eleven_ws.send(json.dumps({
                                "type":     "pong",
                                "event_id": event_id,
                            }))

                    elif event_type == "agent_response":
                        text = data.get("agent_response_event", {}).get("agent_response", "")
                        logger.info(f"[ELEVEN GPT] '{text}'")

                    elif event_type == "user_transcript":
                        text = data.get("user_transcription_event", {}).get("user_transcript", "")
                        logger.info(f"[ELEVEN USUARIO] '{text}'")

                    elif event_type == "error":
                        logger.error(f"[ELEVEN ERROR] {data}")

                    else:
                        logger.debug(f"[ELEVEN EVENTO] {event_type}")

            except Exception as e:
                logger.error(f"[ELEVEN] eleven_to_telnyx error: {e}")

        await asyncio.gather(telnyx_to_eleven(), eleven_to_telnyx())

    except Exception as e:
        logger.exception(f"[TELNYX] Error en media_stream: {e}")
    finally:
        if eleven_ws:
            await eleven_ws.close()
        logger.info("[TELNYX] WebSocket cerrado")


# ---------------------------------------------------------------------------
# Llamada saliente
# ---------------------------------------------------------------------------

class OutboundCallRequest(BaseModel):
    to: str


@router.post("/call")
async def make_call(body: OutboundCallRequest):
    """
    Inicia una llamada saliente usando la API TeXML de Telnyx.
    Cuando la llamada se conecta, Telnyx pide el TeXML a /voice-telnyx/incoming
    que a su vez abre el WebSocket con ElevenLabs.

    Requiere:
      TELNYX_API_KEY       → clave de API de Telnyx
      TELNYX_CONNECTION_ID → application_id de la TeXML Application en Telnyx
      TELNYX_PHONE_NUMBER  → número Telnyx en formato E.164 (+549...)
    """
    if not TELNYX_API_KEY or not TELNYX_CONNECTION_ID or not TELNYX_PHONE_NUMBER:
        raise HTTPException(
            status_code=500,
            detail="Faltan variables: TELNYX_API_KEY, TELNYX_CONNECTION_ID o TELNYX_PHONE_NUMBER.",
        )

    url = f"https://api.telnyx.com/v2/texml/calls/{TELNYX_CONNECTION_ID}"
    payload = {
        "To":   body.to,
        "From": TELNYX_PHONE_NUMBER,
        "Url":  f"{BASE_URL}/voice-telnyx/incoming",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization":  f"Bearer {TELNYX_API_KEY}",
                    "Content-Type":   "application/json",
                    "Accept":         "application/json",
                },
                json=payload,
            )

        if resp.status_code not in (200, 201):
            logger.error(f"[TELNYX] Error llamada saliente: {resp.status_code} {resp.text[:300]}")
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Telnyx respondió {resp.status_code}: {resp.text[:300]}",
            )

        data = resp.json().get("data", {})
        logger.info(f"[TELNYX] Llamada saliente iniciada → to={body.to}, status={data.get('status')}")
        return {
            "status": data.get("status"),
            "to":     body.to,
            "from":   TELNYX_PHONE_NUMBER,
        }

    except httpx.RequestError as e:
        logger.exception(f"[TELNYX] Error de red: {e}")
        raise HTTPException(status_code=502, detail=f"Error conectando con Telnyx: {e}")
