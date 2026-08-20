"""
app/voice/elevenlabs.py

Integración Twilio Media Streams + ElevenLabs Conversational AI.
Twilio manda audio por WebSocket → ElevenLabs lo procesa → responde con voz.

Endpoints:
  POST /voice-eleven/incoming  → webhook Twilio para esta demo
  WS   /voice-eleven/stream    → Media Stream WebSocket
  POST /voice-eleven/call      → llamada saliente
"""

import os
import json
import base64
import audioop
import logging
import asyncio
import httpx
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Connect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice-eleven", tags=["voice-elevenlabs"])

ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = "agent_3701m0dfvnmmemptqcm2jym9wdvv"
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "2KrcT8wE5IyKnWz2nknz")


async def patch_agent_voice():
    """Al arrancar, parchea el agente con la voz de ELEVENLABS_VOICE_ID."""
    if not ELEVENLABS_API_KEY or not ELEVENLABS_AGENT_ID or not ELEVENLABS_VOICE_ID:
        logger.warning("[ELEVEN] patch_agent_voice: faltan variables, no se parchea el agente.")
        return
    url = f"https://api.elevenlabs.io/v1/convai/agents/{ELEVENLABS_AGENT_ID}"
    payload = {
        "conversation_config": {
            "agent": {
                "language": "es-419",
            },
            "tts": {
                "voice_id": ELEVENLABS_VOICE_ID,
                "model_id": "eleven_v3_conversational",
                "voice_settings": {
                    "stability": 0.8,
                    "similarity_boost": 1.0,
                },
            }
        }
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.patch(
                url,
                headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
                json=payload,
            )
        if resp.status_code == 200:
            logger.info(f"[ELEVEN] Agente actualizado — voice_id={ELEVENLABS_VOICE_ID}")
        else:
            logger.error(f"[ELEVEN] Error al parchear agente: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.error(f"[ELEVEN] Exception en patch_agent_voice: {e}")

BASE_URL            = os.getenv("BASE_URL", "")
TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")


# ---------------------------------------------------------------------------
# Helpers de conversión de audio
# ElevenLabs manda/recibe PCM 16kHz — Twilio necesita ulaw 8kHz
# ---------------------------------------------------------------------------

def pcm16k_to_ulaw8k_b64(pcm16k_bytes: bytes) -> str:
    """PCM 16-bit 16kHz → ulaw 8kHz → base64"""
    # Resample 16000 → 8000 (factor 2)
    pcm8k, _ = audioop.ratecv(pcm16k_bytes, 2, 1, 16000, 8000, None)
    # Convertir a ulaw
    ulaw = audioop.lin2ulaw(pcm8k, 2)
    return base64.b64encode(ulaw).decode()


def ulaw8k_b64_to_pcm16k(payload_b64: str) -> bytes:
    """base64 ulaw 8kHz → PCM 16-bit 16kHz"""
    ulaw = base64.b64decode(payload_b64)
    # ulaw → PCM 16-bit 8kHz
    pcm8k = audioop.ulaw2lin(ulaw, 2)
    # Resample 8000 → 16000
    pcm16k, _ = audioop.ratecv(pcm8k, 2, 1, 8000, 16000, None)
    return pcm16k


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Diagnóstico — configuración real del Agent en ElevenLabs
# ---------------------------------------------------------------------------

@router.get("/config")
async def get_agent_config():
    """Consulta la configuración real del Agent configurado en ElevenLabs."""
    if not ELEVENLABS_API_KEY:
        raise HTTPException(status_code=500, detail="ELEVENLABS_API_KEY no configurada.")
    if not ELEVENLABS_AGENT_ID:
        raise HTTPException(status_code=500, detail="ELEVENLABS_AGENT_ID no configurado.")

    url = f"https://api.elevenlabs.io/v1/convai/agents/{ELEVENLABS_AGENT_ID}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"xi-api-key": ELEVENLABS_API_KEY})
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Error conectando con ElevenLabs: {e}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"ElevenLabs respondió {resp.status_code}: {resp.text[:300]}",
        )

    data = resp.json()
    cc   = data.get("conversation_config", {})
    tts  = cc.get("tts", {})
    agent = cc.get("agent", {})
    asr  = cc.get("asr", {})

    return {
        "agent_id":   data.get("agent_id"),
        "agent_name": data.get("name"),
        "voice_id":   tts.get("voice_id"),
        "tts_model":  tts.get("model_id"),
        "output_audio_format": tts.get("agent_output_audio_format"),
        "input_audio_format":  asr.get("user_input_audio_format"),
        "language":   agent.get("language"),
        "first_message": agent.get("first_message"),
    }



@router.post("/incoming")
async def incoming_call(request: Request):
    host = BASE_URL.replace("https://", "").replace("http://", "")
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"wss://{host}/voice-eleven/stream")
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


@router.websocket("/stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    logger.info("[ELEVEN] Twilio WebSocket conectado")

    eleven_ws  = None
    stream_sid = None
    eleven_ready = asyncio.Event()  # se activa cuando ElevenLabs confirma la sesión

    try:
        # Conectar a ElevenLabs Conversational AI
        # ElevenLabs espera PCM 16kHz — nosotros convertimos en ambas direcciones
        eleven_ws = await websockets.connect(
            f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={ELEVENLABS_AGENT_ID}",
            additional_headers={"xi-api-key": ELEVENLABS_API_KEY},
        )
        logger.info("[ELEVEN] ElevenLabs WebSocket conectado")

        # Inicialización: nivel raíz del mensaje, NO dentro de conversation_config_override
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

        # ----------------------------------------------------------------
        # Twilio → ElevenLabs
        # Recibimos ulaw 8kHz de Twilio, convertimos a PCM 16kHz para ElevenLabs
        # ----------------------------------------------------------------
        async def twilio_to_eleven():
            nonlocal stream_sid
            audio_chunks = 0
            try:
                async for message in websocket.iter_text():
                    data  = json.loads(message)
                    event = data.get("event")

                    if event == "start":
                        stream_sid = data["start"]["streamSid"]
                        logger.info(f"[ELEVEN] streamSid={stream_sid}")

                    elif event == "media":
                        # Esperar a que ElevenLabs esté listo antes de mandar audio
                        await eleven_ready.wait()

                        audio_chunks += 1
                        if audio_chunks % 50 == 0:
                            logger.info(f"[ELEVEN] {audio_chunks} chunks enviados")

                        try:
                            # Convertir ulaw 8kHz → PCM 16kHz → base64
                            pcm16k = ulaw8k_b64_to_pcm16k(data["media"]["payload"])
                            pcm_b64 = base64.b64encode(pcm16k).decode()

                            await eleven_ws.send(json.dumps({
                                "user_audio_chunk": pcm_b64,
                            }))
                        except Exception as e:
                            logger.error(f"[ELEVEN] Error convirtiendo audio Twilio→EL: {e}")
                            break

                    elif event == "stop":
                        logger.info(f"[ELEVEN] STREAM STOP. Total chunks: {audio_chunks}")
                        break

                    else:
                        logger.debug(f"[ELEVEN TWILIO EVENT] {event}")

            except WebSocketDisconnect:
                logger.info("[ELEVEN] Twilio desconectado")

        # ----------------------------------------------------------------
        # ElevenLabs → Twilio
        # Recibimos PCM 16kHz de ElevenLabs, convertimos a ulaw 8kHz para Twilio
        # ----------------------------------------------------------------
        async def eleven_to_twilio():
            try:
                async for message in eleven_ws:
                    data       = json.loads(message)
                    event_type = data.get("type")

                    if event_type == "conversation_initiation_metadata":
                        meta = data.get("conversation_initiation_metadata_event", {})
                        conv_id     = meta.get("conversation_id", "?")
                        out_format  = meta.get("agent_output_audio_format", "?")
                        in_format   = meta.get("user_input_audio_format", "?")
                        logger.info(f"[ELEVEN] Sesión iniciada — conversation_id={conv_id}")
                        logger.info(f"[ELEVEN] audio entrada={in_format} | salida={out_format}")
                        logger.info(f"[ELEVEN] voice_id activo: 2KrcT8wE5IyKnWz2nknz esperado — verificá con /voice-eleven/config")
                        eleven_ready.set()

                    elif event_type == "audio":
                        audio_b64 = data.get("audio_event", {}).get("audio_base_64", "")
                        if audio_b64 and stream_sid:
                            try:
                                # PCM 16kHz de ElevenLabs → ulaw 8kHz para Twilio
                                pcm16k_bytes = base64.b64decode(audio_b64)
                                ulaw_b64     = pcm16k_to_ulaw8k_b64(pcm16k_bytes)

                                await websocket.send_text(json.dumps({
                                    "event":     "media",
                                    "streamSid": stream_sid,
                                    "media":     {"payload": ulaw_b64},
                                }))
                            except Exception as e:
                                logger.error(f"[ELEVEN] Error convirtiendo audio EL→Twilio: {e}")

                    elif event_type == "interruption":
                        logger.info("[ELEVEN] Interrupción detectada")
                        if stream_sid:
                            await websocket.send_text(json.dumps({
                                "event":     "clear",
                                "streamSid": stream_sid,
                            }))

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
                        logger.debug(f"[ELEVEN EVENT] {event_type}")

            except Exception as e:
                logger.error(f"[ELEVEN] eleven_to_twilio error: {e}")

        await asyncio.gather(twilio_to_eleven(), eleven_to_twilio())

    except Exception as e:
        logger.exception(f"[ELEVEN] Error en media_stream: {e}")
    finally:
        if eleven_ws:
            await eleven_ws.close()
        logger.info("[ELEVEN] WebSocket cerrado")


# ---------------------------------------------------------------------------
# Llamada saliente
# ---------------------------------------------------------------------------

class OutboundCallRequest(BaseModel):
    to: str


@router.post("/call")
async def make_call(body: OutboundCallRequest):
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
        raise HTTPException(status_code=500, detail="Twilio no configurado correctamente.")
    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        call = client.calls.create(
            to=body.to,
            from_=TWILIO_PHONE_NUMBER,
            url=f"{BASE_URL}/voice-eleven/incoming",
            method="POST",
        )
        logger.info(f"[ELEVEN] Llamada saliente - SID: {call.sid}, To: {body.to}")
        return {"call_sid": call.sid, "status": call.status, "to": body.to}
    except Exception as e:
        logger.exception(f"[ELEVEN] Error iniciando llamada: {e}")
        raise HTTPException(status_code=500, detail=str(e))
