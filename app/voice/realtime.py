"""
app/voice/realtime.py

Integración Twilio Media Streams + OpenAI Realtime API.
Twilio manda audio por WebSocket → OpenAI Realtime lo procesa → responde con voz.
"""

import os
import json
import base64
import logging
import asyncio
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice-realtime"])

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "")

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

VOICE = "shimmer"  # opciones: alloy, echo, fable, onyx, nova, shimmer


@router.post("/incoming")
async def incoming_call(request: Request):
    """Webhook de Twilio — responde con TwiML que conecta al WebSocket."""
    host = BASE_URL.replace("https://", "").replace("http://", "")
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"wss://{host}/voice/stream")
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


@router.websocket("/stream")
async def media_stream(websocket: WebSocket):
    """WebSocket que conecta Twilio Media Streams con OpenAI Realtime API."""
    await websocket.accept()
    logger.info("Twilio WebSocket conectado")

    openai_ws = None
    stream_sid = None

    try:
        openai_ws = await websockets.connect(
            "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01",
            extra_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1",
            },
        )
        logger.info("OpenAI Realtime WebSocket conectado")

        # Inicializar sesión
        await openai_ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "turn_detection": {"type": "server_vad"},
                "input_audio_format": "g711_ulaw",
                "output_audio_format": "g711_ulaw",
                "voice": VOICE,
                "instructions": SYSTEM_PROMPT,
                "modalities": ["text", "audio"],
                "temperature": 0.7,
            }
        }))

        # Saludo inicial
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
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    event = data.get("event")

                    if event == "start":
                        stream_sid = data["start"]["streamSid"]
                        logger.info(f"Stream iniciado: {stream_sid}")

                    elif event == "media":
                        if openai_ws.open:
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": data["media"]["payload"],
                            }))

                    elif event == "stop":
                        logger.info("Stream detenido por Twilio")
                        break

            except WebSocketDisconnect:
                logger.info("Twilio desconectado")

        async def openai_to_twilio():
            try:
                async for message in openai_ws:
                    data = json.loads(message)
                    event_type = data.get("type")

                    if event_type == "response.audio.delta" and data.get("delta"):
                        # Mandar audio a Twilio
                        await websocket.send_text(json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": data["delta"]},
                        }))

                    elif event_type == "response.audio_transcript.delta":
                        logger.info(f"GPT: {data.get('delta', '')}")

                    elif event_type == "input_audio_buffer.speech_started":
                        # Usuario habló — interrumpir respuesta actual
                        await websocket.send_text(json.dumps({
                            "event": "clear",
                            "streamSid": stream_sid,
                        }))

                    elif event_type == "error":
                        logger.error(f"OpenAI error: {data}")

            except Exception as e:
                logger.error(f"Error en openai_to_twilio: {e}")

        await asyncio.gather(twilio_to_openai(), openai_to_twilio())

    except Exception as e:
        logger.exception(f"Error en media_stream: {e}")
    finally:
        if openai_ws:
            await openai_ws.close()
        logger.info("WebSocket cerrado")
