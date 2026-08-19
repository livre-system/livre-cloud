"""
app/voice/gemini_live.py

Integración Twilio Media Streams + Gemini Live API.
Estructura idéntica a realtime.py — solo cambia el backend de IA.

Twilio → PCMU 8kHz → nosotros convertimos → PCM 16kHz → Gemini Live
Gemini Live → PCM 24kHz → nosotros convertimos → PCMU 8kHz → Twilio

SDK probado: google-genai 2.18.1
"""

import os
import re
import json
import base64
import audioop
import logging
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Connect

import google.genai as genai
import google.genai.types as genai_types

from app.core.database import get_cursor
from app.memory.embeddings import get_embedding
import app.chat.repository as repo
from app.memory.service import save_message_to_memory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice-gemini", tags=["voice-gemini"])

BASE_URL            = os.getenv("BASE_URL", "")
GEMINI_API_KEY      = os.getenv("GEMINI_API_KEY", "")
TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
VOICE_SYSTEM_USER   = "voice_calls"
GEMINI_LIVE_MODEL   = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")

SYSTEM_PROMPT = (
    "Sos LIVRE, el asistente de voz de Viaja Livre, una empresa de transporte de pasajeros "
    "en Bahía Blanca, Argentina. Buscá que la dirección coincida con una calle de la ciudad. "
    "Tu único objetivo es tomar pedidos de viaje por teléfono. "
    "Hablás en español argentino. Sin excepciones, de forma clara, concisa y amable. "
    "Nunca usés markdown ni listas. Solo texto natural para hablar. "
    "Respondé siempre en máximo 2 oraciones cortas.\n\n"
    "FLUJO:\n"
    "1. Saludá y preguntá en qué podés ayudar.\n"
    "2. Si quiere un viaje, pedí el origen: ¿Desde dónde salís?\n"
    "3. Cuando diga la dirección, llamá buscar_calle() y confirmá con el usuario.\n"
    "4. Si confirma, pedí el destino: ¿A dónde vas?\n"
    "5. Repetí el destino y confirmá.\n"
    "6. Cerrá con: Perfecto, tu viaje está registrado. ¡Buen viaje!\n"
    "Si no entendés algo, pedí que repitan despacio. Nunca inventes direcciones."
)

# ---------------------------------------------------------------------------
# Audio — igual que realtime.py + conversión de sample rate
# ---------------------------------------------------------------------------

TWILIO_RATE  = 8000
GEMINI_IN    = 16000
GEMINI_OUT   = 24000
VOLUME_BOOST = 5.0

def _build_ulaw_to_linear() -> list:
    table = []
    for u in range(256):
        u_val = ~u & 0xFF
        exponent = (u_val >> 4) & 0x07
        mantissa = u_val & 0x0F
        linear = ((mantissa << 1) + 33) << exponent
        linear -= 33
        if u_val & 0x80:
            linear = -linear
        table.append(linear)
    return table

_ULAW_TO_LINEAR = _build_ulaw_to_linear()
_BIAS = 0x84
_CLIP = 32635

def _linear_to_ulaw(sample: int) -> int:
    sign = 0
    if sample < 0:
        sample = -sample
        sign = 0x80
    if sample > _CLIP:
        sample = _CLIP
    sample += _BIAS
    exponent = 7
    for exp, threshold in enumerate([0x100, 0x200, 0x400, 0x800, 0x1000, 0x2000, 0x4000]):
        if sample < threshold:
            exponent = exp
            break
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF

def _boost_pcmu(payload_b64: str, volume: float = VOLUME_BOOST) -> str:
    """Amplifica audio G.711 μ-law en Python puro — igual que realtime.py."""
    raw = base64.b64decode(payload_b64)
    boosted = bytearray(len(raw))
    for i, byte in enumerate(raw):
        linear = int(_ULAW_TO_LINEAR[byte] * volume)
        linear = max(-32768, min(32767, linear))
        boosted[i] = _linear_to_ulaw(linear)
    return base64.b64encode(bytes(boosted)).decode()

def pcmu_to_pcm16(mulaw_b64: str) -> bytes:
    """PCMU base64 8 kHz → PCM16 16 kHz para Gemini."""
    raw  = base64.b64decode(mulaw_b64)
    pcm8 = audioop.ulaw2lin(raw, 2)
    pcm16, _ = audioop.ratecv(pcm8, 2, 1, TWILIO_RATE, GEMINI_IN, None)
    return pcm16

def pcm24_to_pcmu(pcm24: bytes) -> str:
    """PCM16 24 kHz de Gemini → PCMU base64 8 kHz para Twilio."""
    pcm8, _ = audioop.ratecv(pcm24, 2, 1, GEMINI_OUT, TWILIO_RATE, None)
    mulaw   = audioop.lin2ulaw(pcm8, 2)
    return base64.b64encode(mulaw).decode()

# ---------------------------------------------------------------------------
# Embeddings / buscar_calle
# ---------------------------------------------------------------------------

_embedding_cache: dict[str, list[str]] = {}

def _search_top3_streets(query: str) -> list[str]:
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
            rows   = cur.fetchall()
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
# Tool definition para Gemini
# ---------------------------------------------------------------------------

BUSCAR_CALLE_TOOL = genai_types.Tool(
    function_declarations=[
        genai_types.FunctionDeclaration(
            name="buscar_calle",
            description=(
                "Busca una calle en la base de datos de calles de Bahía Blanca. "
                "Llamá esta función cada vez que el usuario mencione una dirección o calle."
            ),
            parameters=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "query": genai_types.Schema(
                        type=genai_types.Type.STRING,
                        description="La dirección o nombre de calle tal como la dijo el usuario.",
                    )
                },
                required=["query"],
            ),
        )
    ]
)

# ---------------------------------------------------------------------------
# Incoming — TwiML idéntico a realtime.py
# ---------------------------------------------------------------------------

@router.post("/incoming")
async def gemini_incoming(request: Request):
    logger.info("Gemini Live — llamada entrante")
    host = BASE_URL.replace("https://", "").replace("http://", "")
    response = VoiceResponse()
    connect  = Connect()
    connect.stream(url=f"wss://{host}/voice-gemini/stream")
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")

# ---------------------------------------------------------------------------
# WebSocket — estructura idéntica a realtime.py
# ---------------------------------------------------------------------------

@router.websocket("/stream")
async def gemini_stream(websocket: WebSocket):
    await websocket.accept()
    logger.info("[GEMINI] Twilio WebSocket conectado")

    stream_sid = None
    call_sid   = None
    chat_id    = None

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        live_config = genai_types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=SYSTEM_PROMPT,
            tools=[BUSCAR_CALLE_TOOL],
            speech_config=genai_types.SpeechConfig(
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                        voice_name="Aoede"
                    )
                )
            ),
            # VAD automático — configuración mínima probada con gemini-3.1-flash-live-preview
            realtime_input_config=genai_types.RealtimeInputConfig(
                automatic_activity_detection=genai_types.AutomaticActivityDetection(
                    disabled=False,
                ),
            ),
            # Transcripción del audio del usuario
            input_audio_transcription=genai_types.AudioTranscriptionConfig(),
        )

        async with client.aio.live.connect(model=GEMINI_LIVE_MODEL, config=live_config) as session:
            logger.info("[GEMINI] Sesión Live abierta")

            # Saludo inicial — igual que realtime.py dispara response.create
            await session.send(
                input="Saluda al usuario brevemente.",
                end_of_turn=True,
            )

            # Esperar que el saludo termine antes de enviar audio del usuario
            greeting_done    = asyncio.Event()
            gemini_speaking  = asyncio.Event()   # True mientras Gemini emite audio

            # ------------------------------------------------------------------
            # twilio_to_gemini — igual que twilio_to_openai en realtime.py
            # ------------------------------------------------------------------
            async def twilio_to_gemini():
                nonlocal stream_sid, call_sid, chat_id
                audio_chunks = 0
                try:
                    async for message in websocket.iter_text():
                        data  = json.loads(message)
                        event = data.get("event")

                        if event == "start":
                            stream_sid = data["start"]["streamSid"]
                            call_sid   = data["start"].get("callSid", "")
                            logger.info(f"[STREAM START] streamSid={stream_sid}")
                            if call_sid:
                                chat_id = await asyncio.to_thread(
                                    _get_or_create_voice_chat, call_sid
                                )

                        elif event == "media":
                            # No enviar audio hasta que el saludo terminó
                            if not greeting_done.is_set():
                                await greeting_done.wait()
                            # No enviar audio mientras Gemini está hablando (evita eco/auto-interrupción)
                            if gemini_speaking.is_set():
                                continue
                            audio_chunks += 1
                            try:
                                pcm = await asyncio.to_thread(
                                    pcmu_to_pcm16, data["media"]["payload"]
                                )
                                # --- DIAGNÓSTICO DE ENVÍO ---
                                if audio_chunks <= 3:
                                    # Primeros 3 chunks: logueamos todo
                                    frames    = len(pcm) // 2          # PCM16 = 2 bytes/frame
                                    secs      = frames / GEMINI_IN
                                    nonzero   = sum(1 for i in range(0, len(pcm), 2)
                                                    if int.from_bytes(pcm[i:i+2], "little", signed=True) != 0)
                                    logger.info(
                                        f"[AUDIO DIAG #{audio_chunks}] "
                                        f"bytes={len(pcm)} frames={frames} "
                                        f"duracion={secs:.3f}s "
                                        f"frames_nonzero={nonzero} "
                                        f"mime=audio/pcm;rate=16000"
                                    )
                                elif audio_chunks % 50 == 0:
                                    logger.info(f"[AUDIO] {audio_chunks} chunks enviados a Gemini")

                                await session.send_realtime_input(
                                    audio=genai_types.Blob(
                                        data=pcm,
                                        mime_type="audio/pcm;rate=16000",
                                    )
                                )
                            except Exception as exc:
                                logger.error(f"[ERROR] twilio_to_gemini send: {exc}")
                                break

                        elif event == "stop":
                            logger.info(f"[STREAM STOP] Total chunks: {audio_chunks}")
                            break

                        else:
                            logger.info(f"[TWILIO EVENT] {event}")

                except WebSocketDisconnect:
                    logger.info("[TWILIO] Desconectado")

            # ------------------------------------------------------------------
            # gemini_to_twilio — igual que openai_to_twilio en realtime.py
            # ------------------------------------------------------------------
            async def gemini_to_twilio():
                model_text: list[str] = []
                user_text:  list[str] = []
                msg_count  = 0
                try:
                    async for response in session.receive():
                        msg_count += 1

                        # ----------------------------------------------------------
                        # DIAGNÓSTICO SEGURO — inspeccionamos cada mensaje
                        # ----------------------------------------------------------
                        if msg_count <= 20:
                            # Listamos todos los atributos no-privados del objeto
                            attrs = [a for a in dir(response) if not a.startswith("_")]
                            # Campos con valor no-None
                            populated = []
                            for a in attrs:
                                try:
                                    val = getattr(response, a, None)
                                    if val is not None and not callable(val):
                                        populated.append(a)
                                except Exception:
                                    pass
                            logger.info(
                                f"[RECV #{msg_count}] type={type(response).__name__} "
                                f"campos_con_valor={populated}"
                            )

                        # Inspección segura de server_content
                        sc = getattr(response, "server_content", None)
                        if sc is not None and msg_count <= 20:
                            sc_fields = {}
                            for f in ["interrupted", "turn_complete", "model_turn",
                                      "input_transcription", "output_transcription",
                                      "generation_complete"]:
                                v = getattr(sc, f, None)
                                if v is not None:
                                    sc_fields[f] = repr(v)[:120]
                            logger.info(f"[RECV #{msg_count}] server_content fields={sc_fields}")

                        # Inspección segura de voice_activity / VAD
                        va = getattr(response, "voice_activity", None)
                        if va is not None:
                            vtype = getattr(va, "voice_activity_type", None)
                            logger.info(f"[RECV #{msg_count}] voice_activity type={vtype}")

                        # Inspección segura de tool_call
                        tc = getattr(response, "tool_call", None)
                        if tc is not None and msg_count <= 20:
                            logger.info(f"[RECV #{msg_count}] tool_call={repr(tc)[:200]}")

                        # ----------------------------------------------------------
                        # VAD: voice_activity (SDK 2.18.1 confirmado)
                        # LiveServerMessage.voice_activity → VoiceActivity
                        #   └─ voice_activity_type: VoiceActivityType
                        #        ACTIVITY_START | ACTIVITY_END | TYPE_UNSPECIFIED
                        # ----------------------------------------------------------
                        if va is not None:
                            vtype = getattr(va, "voice_activity_type", None)
                            if vtype == genai_types.VoiceActivityType.ACTIVITY_START:
                                logger.info("[GEMINI] speech_started")
                                # Barge-in: limpiar buffer de audio en Twilio
                                if stream_sid:
                                    await websocket.send_text(json.dumps({
                                        "event": "clear",
                                        "streamSid": stream_sid,
                                    }))
                            elif vtype == genai_types.VoiceActivityType.ACTIVITY_END:
                                logger.info("[GEMINI] speech_stopped")

                        # ----------------------------------------------------------
                        # Interrupción explícita (server_content.interrupted)
                        # Solo si interrupted es explícitamente True — no truthy vacío
                        # ----------------------------------------------------------
                        if sc and getattr(sc, "interrupted", None) is True:
                            logger.info("[GEMINI] interrupted real")
                            if stream_sid:
                                await websocket.send_text(json.dumps({
                                    "event": "clear",
                                    "streamSid": stream_sid,
                                }))

                        # ----------------------------------------------------------
                        # Audio de respuesta de Gemini
                        # ----------------------------------------------------------
                        if sc and getattr(sc, "model_turn", None):
                            gemini_speaking.set()   # Gemini está hablando → bloquear input
                            for part in (getattr(sc.model_turn, "parts", None) or []):
                                # Audio inline
                                if getattr(part, "inline_data", None) and part.inline_data.data:
                                    mulaw_b64 = await asyncio.to_thread(
                                        pcm24_to_pcmu, part.inline_data.data
                                    )
                                    mulaw_b64 = _boost_pcmu(mulaw_b64)
                                    if stream_sid:
                                        await websocket.send_text(json.dumps({
                                            "event": "media",
                                            "streamSid": stream_sid,
                                            "media": {"payload": mulaw_b64},
                                        }))
                                # Texto (transcripción de salida, si llega)
                                if getattr(part, "text", None):
                                    model_text.append(part.text)
                                    logger.info(f"[GEMINI texto] {part.text}")

                        # ----------------------------------------------------------
                        # Transcripción del usuario (input_transcription)
                        # ----------------------------------------------------------
                        if sc and getattr(sc, "input_transcription", None):
                            tr = sc.input_transcription
                            if getattr(tr, "text", None):
                                user_text.append(tr.text)
                                if getattr(tr, "finished", False):
                                    logger.info(f"[USUARIO] {''.join(user_text)}")

                        # ----------------------------------------------------------
                        # Function calling
                        # ----------------------------------------------------------
                        if tc:
                            for fc in (getattr(tc, "function_calls", None) or []):
                                if fc.name == "buscar_calle":
                                    query = fc.args.get("query", "")
                                    logger.info(f"[TOOL] buscar_calle({query!r})")
                                    candidates = await asyncio.to_thread(
                                        _search_top3_streets, query
                                    )
                                    if candidates:
                                        result = (
                                            f"Calles encontradas: {', '.join(candidates)}. "
                                            "Preguntale al usuario cuál es la correcta."
                                        )
                                    else:
                                        result = (
                                            "No se encontró la calle. "
                                            "Pedile al usuario que deletree la calle."
                                        )
                                    await session.send(
                                        input=genai_types.LiveClientToolResponse(
                                            function_responses=[
                                                genai_types.FunctionResponse(
                                                    id=fc.id,
                                                    name=fc.name,
                                                    response={"result": result},
                                                )
                                            ]
                                        )
                                    )

                        # ----------------------------------------------------------
                        # generation_complete → Gemini terminó de hablar, habilitar input
                        # ----------------------------------------------------------
                        if sc and getattr(sc, "generation_complete", False):
                            gemini_speaking.clear()   # Gemini terminó → aceptar audio del usuario
                            if not greeting_done.is_set():
                                greeting_done.set()
                                logger.info("[GEMINI] Saludo completo (generation_complete) — activando audio del usuario")
                            else:
                                logger.info("[GEMINI] Respuesta completa — escuchando al usuario")

                        # ----------------------------------------------------------
                        # Fin de turno — guardar en chat
                        # ----------------------------------------------------------
                        if sc and getattr(sc, "turn_complete", False):
                            gemini_speaking.clear()   # fallback por si generation_complete no llegó
                            if not greeting_done.is_set():
                                greeting_done.set()
                                logger.info("[GEMINI] Saludo completo (turn_complete) — activando audio del usuario")

                            full_user  = "".join(user_text).strip()
                            full_model = "".join(model_text).strip()
                            if full_user or full_model:
                                logger.info(
                                    f"[GEMINI] turn_complete — usuario: '{full_user}' | modelo: '{full_model}'"
                                )

                            if chat_id:
                                if user_text:
                                    txt = " ".join(user_text).strip()
                                    user_text.clear()
                                    if txt:
                                        await asyncio.to_thread(repo.save_message, chat_id, "user", txt)
                                        await asyncio.to_thread(
                                            save_message_to_memory, VOICE_SYSTEM_USER, "user", txt
                                        )
                                if model_text:
                                    txt = " ".join(model_text).strip()
                                    model_text.clear()
                                    if txt:
                                        await asyncio.to_thread(repo.save_message, chat_id, "assistant", txt)
                                        await asyncio.to_thread(
                                            save_message_to_memory, VOICE_SYSTEM_USER, "assistant", txt
                                        )

                except Exception as e:
                    logger.error(f"[ERROR] gemini_to_twilio: {e}", exc_info=True)

            # Mismo patrón que realtime.py
            await asyncio.gather(twilio_to_gemini(), gemini_to_twilio())

    except Exception as e:
        logger.exception(f"Error en gemini_stream: {e}")
    finally:
        logger.info("[GEMINI] WebSocket cerrado")


# ---------------------------------------------------------------------------
# Outbound call
# ---------------------------------------------------------------------------

class OutboundCallRequest(BaseModel):
    to: str

@router.post("/call")
async def make_call(body: OutboundCallRequest):
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
        raise HTTPException(status_code=500, detail="Twilio no configurado.")
    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        call = client.calls.create(
            to=body.to,
            from_=TWILIO_PHONE_NUMBER,
            url=f"{BASE_URL}/voice-gemini/incoming",
            method="POST",
        )
        logger.info(f"Llamada saliente — SID: {call.sid}, To: {body.to}")
        return {"call_sid": call.sid, "status": call.status, "to": body.to}
    except Exception as e:
        logger.exception(f"Error iniciando llamada: {e}")
        raise HTTPException(status_code=500, detail=str(e))
