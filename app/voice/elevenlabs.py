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
import logging
import asyncio
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Connect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice-eleven", tags=["voice-elevenlabs"])

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = "agent_3701m0dfvnmmemptqcm2jym9wdvv"

BASE_URL = os.getenv("BASE_URL", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

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

    eleven_ws = None
    stream_sid = None

    try:
        # Conectar a ElevenLabs Conversational AI
        eleven_ws = await websockets.connect(
            f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={ELEVENLABS_AGENT_ID}",
            additional_headers={
                "xi-api-key": ELEVENLABS_API_KEY,
            },
        )
        logger.info("[ELEVEN] ElevenLabs WebSocket conectado")

        # Configurar el formato de audio para Twilio (PCMU 8kHz)
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
            "custom_llm_extra_body": {},
        }))

        async def twilio_to_eleven():
            nonlocal stream_sid
            audio_chunks = 0
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    event = data.get("event")

                    if event == "start":
                        stream_sid = data["start"]["streamSid"]
                        logger.info(f"[ELEVEN] streamSid={stream_sid}")

                    elif event == "media":
                        audio_chunks += 1
                        if audio_chunks % 50 == 0:
                            logger.info(f"[ELEVEN] {audio_chunks} chunks enviados a ElevenLabs")
                        try:
                            # ElevenLabs espera audio en base64 ulaw 8kHz
                            await eleven_ws.send(json.dumps({
                                "user_audio_chunk": data["media"]["payload"],
                            }))
                        except Exception:
                            break

                    elif event == "stop":
                        logger.info(f"[ELEVEN] STREAM STOP. Total chunks: {audio_chunks}")
                        break

                    else:
                        logger.debug(f"[ELEVEN TWILIO EVENT] {event}")

            except WebSocketDisconnect:
                logger.info("[ELEVEN] Twilio desconectado")

        async def eleven_to_twilio():
            try:
                async for message in eleven_ws:
                    data = json.loads(message)
                    event_type = data.get("type")

                    if event_type == "conversation_initiation_metadata":
                        logger.info(f"[ELEVEN] Conversación iniciada: {data}")

                    elif event_type == "audio":
                        # Audio de ElevenLabs → Twilio
                        audio_b64 = data.get("audio_event", {}).get("audio_base_64", "")
                        if audio_b64 and stream_sid:
                            await websocket.send_text(json.dumps({
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": audio_b64},
                            }))

                    elif event_type == "interruption":
                        # Usuario interrumpió → limpiar audio en cola
                        logger.info("[ELEVEN] Interrupción detectada")
                        if stream_sid:
                            await websocket.send_text(json.dumps({
                                "event": "clear",
                                "streamSid": stream_sid,
                            }))

                    elif event_type == "ping":
                        # Responder ping para mantener conexión viva
                        event_id = data.get("ping_event", {}).get("event_id")
                        if event_id:
                            await eleven_ws.send(json.dumps({
                                "type": "pong",
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
