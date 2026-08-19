"""
app/voice/realtime.py

Integración Twilio Media Streams + OpenAI Realtime API.
Twilio manda audio por WebSocket → OpenAI Realtime lo procesa → responde con voz.
"""

import os
import json
import logging
import asyncio
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Connect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice-realtime"])

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

SYSTEM_PROMPT = (
    "Sos LIVRE IA, el asistente de voz de Viaja Livre, una empresa de transporte de pasajeros "
    "en Bahía Blanca, Argentina. "
    "Tu único objetivo es tomar pedidos de viaje por teléfono. "
    "Hablás en español rioplatense, de forma clara, concisa y amable. "
    "Nunca usés markdown ni listas. Solo texto natural para hablar. "
    "Respondé siempre en máximo 2 oraciones cortas. "
    "\n\nFLUJO:\n"
    "1. Saludá y preguntá en qué podés ayudar.\n"
    "2. Si quiere un viaje, pedí el origen: '¿Desde dónde salís?'\n"
    "3. Cuando diga la dirección, repetila y confirmá: '¿Es Rivadavia 500?'\n"
    "4. Si confirma, pedí el destino: '¿A dónde vas?'\n"
    "5. Repetí el destino y confirmá.\n"
    "6. Cerrá con: 'Perfecto, tu viaje está registrado. ¡Buen viaje!'\n"
    "\nSi no entendés algo, pedí que repitan despacio. Nunca inventes direcciones."
)

VOICE = "shimmer"


@router.post("/incoming")
async def incoming_call(request: Request):
    host = BASE_URL.replace("https://", "").replace("http://", "")
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"wss://{host}/voice/stream")
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


@router.websocket("/stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    logger.info("Twilio WebSocket conectado")

    openai_ws = None
    stream_sid = None

    try:
        openai_ws = await websockets.connect(
            "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1",
            additional_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            },
        )
        logger.info("OpenAI Realtime WebSocket conectado")

        await openai_ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": "gpt-realtime-2.1",
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcmu"},
                        "turn_detection": {"type": "server_vad"},
                    },
                    "output": {
                        "format": {"type": "audio/pcmu"},
                        "voice": VOICE,
                    }
                },
                "instructions": SYSTEM_PROMPT,
            }
        }))

        await openai_ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Saluda al usuario brevemente."}],
            }
        }))
        await openai_ws.send(json.dumps({"type": "response.create"}))

        async def twilio_to_openai():
            nonlocal stream_sid
            audio_chunks = 0
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    event = data.get("event")
                    if event == "start":
                        stream_sid = data["start"]["streamSid"]
                        logger.info(f"[STREAM START] streamSid={stream_sid}")
                    elif event == "media":
                        audio_chunks += 1
                        if audio_chunks % 50 == 0:
                            logger.info(f"[AUDIO] {audio_chunks} chunks enviados a OpenAI")
                        try:
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": data["media"]["payload"],
                            }))
                        except Exception:
                            break
                    elif event == "stop":
                        logger.info(f"[STREAM STOP] Total chunks: {audio_chunks}")
                        break
                    else:
                        logger.info(f"[TWILIO EVENT] {event}")
            except WebSocketDisconnect:
                logger.info("[TWILIO] Desconectado")

        async def openai_to_twilio():
            gpt_text = []
            try:
                async for message in openai_ws:
                    data = json.loads(message)
                    event_type = data.get("type")

                    if event_type == "session.created":
                        logger.info("[OPENAI] Sesión creada")
                    elif event_type == "session.updated":
                        logger.info("[OPENAI] Sesión configurada")
                    elif event_type == "input_audio_buffer.speech_started":
                        logger.info("[OPENAI] Usuario empezó a hablar")
                        await websocket.send_text(json.dumps({
                            "event": "clear",
                            "streamSid": stream_sid,
                        }))
                    elif event_type == "input_audio_buffer.speech_stopped":
                        logger.info("[OPENAI] Usuario dejó de hablar")
                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        user_text = data.get("transcript", "")
                        logger.info(f"[USUARIO] '{user_text}'")
                    elif event_type == "response.created":
                        logger.info("[OPENAI] Generando respuesta")
                    elif event_type == "response.audio_transcript.delta":
                        gpt_text.append(data.get("delta", ""))
                    elif event_type == "response.audio_transcript.done":
                        logger.info(f"[GPT] '{''.join(gpt_text)}'")
                        gpt_text = []
                    elif event_type == "response.output_audio.delta" and data.get("delta"):
                        logger.info("[AUDIO OUT] Audio recibido de OpenAI")
                        await websocket.send_text(json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": data["delta"]},
                        }))
                    elif event_type == "response.output_audio.done":
                        logger.info("[OPENAI] Audio de respuesta terminado")
                    elif event_type == "response.done":
                        logger.info("[OPENAI] Respuesta completada")
                    elif event_type == "error":
                        logger.error(f"[OPENAI ERROR] {data}")
            except Exception as e:
                logger.error(f"[ERROR] openai_to_twilio: {e}")

        await asyncio.gather(twilio_to_openai(), openai_to_twilio())

    except Exception as e:
        logger.exception(f"Error en media_stream: {e}")
    finally:
        if openai_ws:
            await openai_ws.close()
        logger.info("WebSocket cerrado")


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
            url=f"{BASE_URL}/voice/incoming",
            method="POST",
        )
        logger.info(f"Llamada saliente iniciada - SID: {call.sid}, To: {body.to}")
        return {"call_sid": call.sid, "status": call.status, "to": body.to}
    except Exception as e:
        logger.exception(f"Error iniciando llamada saliente: {e}")
        raise HTTPException(status_code=500, detail=str(e))
