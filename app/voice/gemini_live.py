"""
app/voice/gemini_live.py

Integración Twilio ↔ Gemini Live API para LIVRE.
Endpoints paralelos al flujo OpenAI — no toca /voice/incoming ni /voice/respond.

Flujo:
  Twilio → POST /voice-gemini/incoming → TwiML con <Connect><Stream>
         → WS  /voice-gemini/stream   → Gemini Live (audio bidireccional)

Audio:
  Twilio → PCMU 8kHz → audioop.ulaw2lin + resample → PCM 16-bit 16kHz → Gemini
  Gemini → PCM 24kHz → resample → PCM 8kHz → audioop.lin2ulaw → Twilio

Function calling:
  Gemini detecta dirección → llama buscar_calle() → nosotros ejecutamos
  _search_top3_streets() en PostgreSQL → devolvemos resultados → Gemini confirma
"""

import os
import re
import json
import base64
import asyncio
import audioop
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

# Gemini (SDK nuevo — google-genai)
import google.genai as genai
import google.genai.types as genai_types

# DB y embeddings (misma lógica que router.py)
from app.core.database import get_cursor
from app.memory.embeddings import get_embedding
import app.chat.repository as repo
from app.memory.service import save_message_to_memory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice-gemini", tags=["voice-gemini"])

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL       = os.getenv("BASE_URL", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
VOICE_SYSTEM_USER = "voice_calls"

# Modelo Live disponible — cambiar a gemini-2.5-flash-live cuando esté GA
GEMINI_LIVE_MODEL = "gemini-3.1-flash-live-preview"

# El cliente se inicializa con api_key en cada sesión (SDK nuevo)

# ---------------------------------------------------------------------------
# Caché de embeddings (igual que router.py)
# ---------------------------------------------------------------------------

_embedding_cache: dict[str, list[str]] = {}
_STREET_HINTS: str | None = None


def _get_cached_hints() -> str:
    global _STREET_HINTS
    if _STREET_HINTS is None:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT name FROM street_embeddings ORDER BY name")
                rows = cur.fetchall()
                _STREET_HINTS = ", ".join(r["name"] for r in rows)
                logger.info(f"Gemini Live — hints cargados: {len(rows)} calles")
        except Exception as e:
            logger.warning(f"No se pudieron cargar hints: {e}")
            _STREET_HINTS = ""
    return _STREET_HINTS


def _search_top3_streets(query: str) -> list[str]:
    """Busca las top-3 calles más similares (mismo código que router.py)."""
    street_part = re.sub(r'\b\d+\b', '', query).strip().lower()
    if not street_part:
        return []
    if street_part in _embedding_cache:
        return _embedding_cache[street_part]
    embedding = get_embedding(street_part)
    if not embedding:
        return []
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT name, 1 - (embedding <=> %s::vector) AS similarity
                FROM street_embeddings
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT 3
            """, (str(embedding), str(embedding)))
            rows = cur.fetchall()
            result = [r["name"] for r in rows if r["similarity"] > 0.5]
            _embedding_cache[street_part] = result
            return result
    except Exception as e:
        logger.warning(f"Error buscando calles: {e}")
    return []


# ---------------------------------------------------------------------------
# Chat helpers
# ---------------------------------------------------------------------------

def _get_or_create_voice_chat(call_sid: str) -> str:
    with get_cursor() as cur:
        cur.execute(
            "SELECT chat_id FROM chats WHERE user_id = %s AND title = %s LIMIT 1",
            [VOICE_SYSTEM_USER, f"Llamada {call_sid}"],
        )
        row = cur.fetchone()
        if row:
            return row["chat_id"]
    chat = repo.create_chat(VOICE_SYSTEM_USER)
    repo.update_chat_title(chat["chat_id"], VOICE_SYSTEM_USER, f"Llamada {call_sid}")
    return chat["chat_id"]


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

TWILIO_RATE  = 8000   # Hz — Twilio media stream
GEMINI_IN    = 16000  # Hz — Gemini input espera PCM 16kHz
GEMINI_OUT   = 24000  # Hz — Gemini output devuelve PCM 24kHz


def twilio_to_gemini(mulaw_b64: str) -> bytes:
    """PCMU base64 de Twilio → PCM 16-bit 16kHz listo para Gemini."""
    mulaw_bytes = base64.b64decode(mulaw_b64)
    pcm8 = audioop.ulaw2lin(mulaw_bytes, 2)           # μ-law → PCM 16-bit 8kHz
    pcm16, _ = audioop.ratecv(pcm8, 2, 1, TWILIO_RATE, GEMINI_IN, None)
    return pcm16


def gemini_to_twilio(pcm24_bytes: bytes) -> str:
    """PCM 24kHz de Gemini → PCMU base64 para Twilio."""
    pcm8, _ = audioop.ratecv(pcm24_bytes, 2, 1, GEMINI_OUT, TWILIO_RATE, None)
    mulaw = audioop.lin2ulaw(pcm8, 2)
    return base64.b64encode(mulaw).decode("utf-8")


# ---------------------------------------------------------------------------
# Definición de herramienta para Gemini
# ---------------------------------------------------------------------------

BUSCAR_CALLE_TOOL = {
    "function_declarations": [
        {
            "name": "buscar_calle",
            "description": (
                "Busca una calle en la base de datos de calles de Bahía Blanca. "
                "Llamá esta función cada vez que el usuario mencione una dirección "
                "o nombre de calle. Devuelve las coincidencias más probables."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "La dirección o nombre de calle tal como la dijo el usuario.",
                    }
                },
                "required": ["query"],
            },
        }
    ]
}

# ---------------------------------------------------------------------------
# System prompt (mismo espíritu que router.py)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Sos LIVRE IA, la asistente de voz de Livre Transportes en Bahía Blanca, Argentina.\n"
    "Respondés llamadas telefónicas de pasajeros que quieren pedir un viaje en taxi.\n\n"
    "INSTRUCCIONES:\n"
    "- Hablá en español argentino, de forma natural y concisa.\n"
    "- No uses markdown, listas ni asteriscos. Solo texto para hablar.\n"
    "- Máximo 2 oraciones por respuesta.\n"
    "- Cuando el usuario mencione una dirección, SIEMPRE llamá la función buscar_calle().\n"
    "- Confirmá la calle encontrada con el usuario antes de continuar.\n"
    "- Si buscar_calle() no devuelve resultados, pedile que deletree la calle.\n\n"
    "FLUJO DE PEDIDO:\n"
    "1. Saludar y preguntar en qué podés ayudar.\n"
    "2. Preguntar el punto de origen (¿Desde dónde salís?).\n"
    "3. Confirmar la calle de origen con buscar_calle().\n"
    "4. Preguntar el destino (¿A dónde vas?).\n"
    "5. Confirmar la calle de destino con buscar_calle().\n"
    "6. Repetir origen y destino confirmados, y cerrar la llamada.\n"
)

# ---------------------------------------------------------------------------
# Endpoint HTTP — genera TwiML con <Stream>
# ---------------------------------------------------------------------------

@router.post("/incoming")
async def gemini_incoming(request: Request):
    """
    Twilio llama a este endpoint cuando entra una llamada.
    Devuelve TwiML que abre un WebSocket Media Stream hacia /voice-gemini/stream.
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    logger.info(f"Gemini Live — llamada entrante CallSid={call_sid}")

    ws_url = BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
    stream_url = f"{ws_url}/voice-gemini/stream"

    response = VoiceResponse()
    connect = Connect()
    stream = Stream(url=stream_url)
    stream.parameter(name="CallSid", value=call_sid)
    connect.append(stream)
    response.append(connect)

    return Response(content=str(response), media_type="application/xml")


# ---------------------------------------------------------------------------
# Endpoint WebSocket — sesión Gemini Live
# ---------------------------------------------------------------------------

@router.websocket("/stream")
async def gemini_stream(websocket: WebSocket):
    """
    WebSocket que Twilio abre para streaming de audio.
    Conecta con Gemini Live API de forma bidireccional.
    """
    await websocket.accept()
    logger.info("Gemini Live — WebSocket abierto")

    call_sid: str = ""
    stream_sid: str = ""
    chat_id: str | None = None

    # Buffer para acumular texto del modelo (para guardar en chat)
    model_text_buffer: list[str] = []
    user_text_buffer: list[str] = []

    try:
        # --- Inicializar sesión Gemini Live ---
        client = genai.Client(api_key=GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", ""))

        live_config = genai_types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=SYSTEM_PROMPT,
            tools=[BUSCAR_CALLE_TOOL],
            speech_config=genai_types.SpeechConfig(
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                        voice_name="Aoede"  # Voz femenina disponible en Live
                    )
                )
            ),
            # VAD automático — Gemini detecta cuando el usuario termina de hablar
            realtime_input_config=genai_types.RealtimeInputConfig(
                turn_coverage=genai_types.TurnCoverage.TURN_INCLUDES_ALL_INPUT,
            ),
        )

        async with client.aio.live.connect(
            model=GEMINI_LIVE_MODEL, config=live_config
        ) as session:

            logger.info("Gemini Live — sesión abierta")

            async def receive_from_twilio():
                """Lee mensajes de Twilio y los envía a Gemini."""
                nonlocal call_sid, stream_sid, chat_id

                async for message in websocket.iter_text():
                    data = json.loads(message)
                    event = data.get("event")

                    if event == "start":
                        call_sid   = data["start"].get("callSid", "")
                        stream_sid = data["start"].get("streamSid", "")
                        logger.info(f"Stream iniciado — call={call_sid}")
                        chat_id = await asyncio.to_thread(
                            _get_or_create_voice_chat, call_sid
                        )
                        # Mandar saludo inicial a Gemini para que arranque
                        await session.send(
                            input="Iniciá la conversación saludando al pasajero.",
                            end_of_turn=True,
                        )

                    elif event == "media":
                        # Audio del usuario → convertir y enviar a Gemini
                        mulaw_b64 = data["media"]["payload"]
                        pcm_bytes = await asyncio.to_thread(twilio_to_gemini, mulaw_b64)
                        await session.send(
                            input=genai_types.LiveClientRealtimeInput(
                                audio=genai_types.Blob(
                                    data=pcm_bytes,
                                    mime_type="audio/pcm;rate=16000",
                                )
                            )
                        )
                        if not hasattr(receive_from_twilio, "_audio_count"):
                            receive_from_twilio._audio_count = 0
                        receive_from_twilio._audio_count += 1
                        if receive_from_twilio._audio_count % 50 == 0:
                            logger.info(f"[AUDIO] {receive_from_twilio._audio_count} chunks enviados a Gemini")

                    elif event == "stop":
                        logger.info("Stream detenido por Twilio")
                        break

            async def receive_from_gemini():
                """Lee respuestas de Gemini y las manda a Twilio."""
                nonlocal model_text_buffer, user_text_buffer

                async for response in session.receive():
                    logger.info(f"[GEMINI RAW] sc={bool(response.server_content)} tool={bool(response.tool_call)}")

                    # --- Interrupción: usuario empezó a hablar → limpiar buffer Twilio ---
                    if (
                        response.server_content
                        and getattr(response.server_content, "interrupted", False)
                    ):
                        logger.info("[GEMINI] Usuario interrumpió — limpiando buffer Twilio")
                        if stream_sid:
                            await websocket.send_text(json.dumps({
                                "event": "clear",
                                "streamSid": stream_sid,
                            }))

                    # --- Audio de respuesta (viene como inline_data en las parts) ---
                    if response.server_content and response.server_content.model_turn:
                        for part in response.server_content.model_turn.parts:
                            # Audio
                            if hasattr(part, "inline_data") and part.inline_data and part.inline_data.data:
                                audio_bytes = part.inline_data.data
                                mime = getattr(part.inline_data, "mime_type", "?")
                                logger.info(f"[GEMINI AUDIO] {len(audio_bytes)} bytes, mime={mime}")
                                try:
                                    mulaw_b64 = await asyncio.to_thread(gemini_to_twilio, audio_bytes)
                                    if stream_sid:
                                        await websocket.send_text(json.dumps({
                                            "event": "media",
                                            "streamSid": stream_sid,
                                            "media": {"payload": mulaw_b64},
                                        }))
                                        logger.info(f"[TWILIO] Audio enviado: {len(mulaw_b64)} chars")
                                    else:
                                        logger.warning("[TWILIO] stream_sid vacío, audio descartado")
                                except Exception as e:
                                    logger.error(f"[AUDIO ERROR] {e}")
                            # Texto (para guardar en chat)
                            if hasattr(part, "text") and part.text:
                                model_text_buffer.append(part.text)
                                logger.info(f"[GEMINI] {part.text}")

                    # --- Transcripción del usuario ---
                    if hasattr(response, "input_transcription") and response.input_transcription:
                        user_text_buffer.append(response.input_transcription)
                        logger.info(f"[USUARIO] {response.input_transcription}")

                    # --- Function calling ---
                    if response.tool_call:
                        for fc in response.tool_call.function_calls:
                            if fc.name == "buscar_calle":
                                query = fc.args.get("query", "")
                                logger.info(f"buscar_calle({query!r})")

                                candidates = await asyncio.to_thread(
                                    _search_top3_streets, query
                                )

                                if candidates:
                                    result_text = (
                                        f"Calles encontradas: {', '.join(candidates)}. "
                                        "Preguntale al usuario cuál es la correcta, "
                                        "empezando por la primera."
                                    )
                                else:
                                    result_text = (
                                        "No se encontró la calle en la base de datos. "
                                        "Pedile al usuario que deletree la calle."
                                    )

                                await session.send(
                                    input=genai_types.LiveClientToolResponse(
                                        function_responses=[
                                            genai_types.FunctionResponse(
                                                id=fc.id,
                                                name=fc.name,
                                                response={"result": result_text},
                                            )
                                        ]
                                    )
                                )
                                logger.info(f"buscar_calle → {result_text[:80]}")

                    # --- Fin de turno: guardar en chat ---
                    if response.server_content and response.server_content.turn_complete:
                        logger.info("[GEMINI] Turno completo")
                        if chat_id:
                            if user_text_buffer:
                                user_text = " ".join(user_text_buffer).strip()
                                user_text_buffer.clear()
                                if user_text:
                                    await asyncio.to_thread(
                                        repo.save_message, chat_id, "user", user_text
                                    )
                                    await asyncio.to_thread(
                                        save_message_to_memory,
                                        VOICE_SYSTEM_USER, "user", user_text,
                                    )
                            if model_text_buffer:
                                model_text = " ".join(model_text_buffer).strip()
                                model_text_buffer.clear()
                                if model_text:
                                    await asyncio.to_thread(
                                        repo.save_message, chat_id, "assistant", model_text
                                    )
                                    await asyncio.to_thread(
                                        save_message_to_memory,
                                        VOICE_SYSTEM_USER, "assistant", model_text,
                                    )

            # Correr ambos loops en paralelo
            await asyncio.gather(
                receive_from_twilio(),
                receive_from_gemini(),
            )

    except WebSocketDisconnect:
        logger.info("Gemini Live — WebSocket cerrado por cliente")
    except Exception as e:
        logger.exception(f"Gemini Live — error en sesión: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
