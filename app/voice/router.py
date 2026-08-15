"""
app/voice/router.py

Endpoints para el flujo de llamadas entrantes de Twilio.

Twilio webhooks:
  POST /voice/incoming   → primera respuesta (saludo + Record)
  POST /voice/respond    → recibe grabación → Whisper → GPT-4o → TTS Nova → reproduce

Estos endpoints NO requieren JWT porque Twilio los llama directamente.
"""

import os
import io
import logging
import httpx
from fastapi import APIRouter, Form, Request, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Gather, Play, Record
from openai import AsyncOpenAI

from app.core.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
from app.voice.twilio_handler import build_error_response
from app.llm.groq import GroqProvider
from app.llm.factory import get_llm_provider
from app.context.provider import get_livre_sources
from app.compiler.base_compiler import compile_base_context
from app.compiler.style_compiler import compile_style_context
from app.core.database import get_cursor
from app.memory.service import save_message_to_memory
import app.chat.repository as repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

BASE_URL = os.getenv("BASE_URL", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
VOICE_SYSTEM_USER = "voice_calls"

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


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


def _build_voice_system_prompt() -> str:
    sources = get_livre_sources()
    base_context = compile_base_context(sources)
    style_context = compile_style_context(sources)

    return (
        f"Fecha y hora actual: {base_context['fecha_actual']}\n\n"
        f"Contexto:\n{style_context['sources']['style']}\n\n"
        "INSTRUCCIONES PARA LLAMADA TELEFÓNICA:\n"
        "- Estás respondiendo una llamada de voz. Sé conciso y claro.\n"
        "- No uses markdown, listas ni asteriscos. Solo texto natural para hablar.\n"
        "- Respondé en máximo 3 oraciones cortas.\n"
        "- Hablá en español argentino.\n"
    )


async def _transcribe_audio(recording_url: str) -> str:
    """Descarga el audio de Twilio y lo transcribe con Whisper."""
    auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{recording_url}.mp3", auth=auth, timeout=30)
        resp.raise_for_status()
        audio_bytes = resp.content

    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "audio.mp3"

    transcription = await openai_client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
        language="es",
    )
    return transcription.text.strip()


async def _generate_reply_gpt(transcript: str, chat_id: str) -> str:
    """Genera la respuesta con GPT-4o."""
    history = repo.get_messages(chat_id, VOICE_SYSTEM_USER)
    system_prompt = _build_voice_system_prompt()

    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history[-10:]]
    messages.append({"role": "user", "content": transcript})

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=150,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error con GPT-4o, usando fallback: {e}")
        # Fallback a Gemini/Groq
        try:
            llm = get_llm_provider()
            return llm.generate(messages)
        except Exception:
            groq = GroqProvider()
            return groq.generate(messages)


async def _synthesize_speech(text: str) -> bytes:
    """Sintetiza el texto con OpenAI TTS voz Nova."""
    response = await openai_client.audio.speech.create(
        model="tts-1",
        voice="nova",
        input=text,
        response_format="mp3",
    )
    return response.content


AUDIO_DIR = "/data/uploads/voice_audio"
os.makedirs(AUDIO_DIR, exist_ok=True)


def _save_audio(key: str, audio_bytes: bytes) -> str:
    """Guarda el audio en disco y retorna la URL pública."""
    filename = f"{key}.mp3"
    filepath = os.path.join(AUDIO_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(audio_bytes)
    return f"{BASE_URL}/uploads/voice_audio/{filename}"


# ---------------------------------------------------------------------------
# Endpoints de voz
# ---------------------------------------------------------------------------

@router.post("/incoming")
async def incoming_call(
    request: Request,
    CallSid: str = Form(default=""),
    From: str = Form(default=""),
):
    """Webhook inicial — saluda con TTS Nova y graba la voz del usuario."""
    logger.info(f"Llamada entrante - CallSid: {CallSid}, From: {From}")

    try:
        # Sintetizar saludo con Nova
        saludo = "Hola, soy LIVRE IA. ¿En qué te puedo ayudar?"
        audio_bytes = await _synthesize_speech(saludo)
        audio_url = _save_audio(CallSid, audio_bytes)

        response = VoiceResponse()
        response.play(audio_url)
        response.record(
            action=f"{BASE_URL}/voice/respond?call_sid={CallSid}",
            method="POST",
            max_length=30,
            timeout=5,
            finish_on_key="#",
            play_beep=False,
        )

        return Response(content=str(response), media_type="application/xml")

    except Exception as e:
        logger.exception(f"Error en incoming: {e}")
        return Response(content=build_error_response(), media_type="application/xml")


@router.post("/respond")
async def respond_to_speech(
    request: Request,
    RecordingUrl: str = Form(default=""),
    RecordingSid: str = Form(default=""),
    call_sid: str = "",
    CallSid: str = Form(default=""),
):
    """Recibe grabación → Whisper → GPT-4o → TTS Nova → reproduce."""
    effective_call_sid = call_sid or CallSid
    logger.info(f"Grabación recibida - CallSid: {effective_call_sid}, URL: {RecordingUrl}")

    if not RecordingUrl:
        response = VoiceResponse()
        audio_bytes = await _synthesize_speech("No escuché bien. ¿Podés repetir?")
        audio_url = _save_audio(f"{effective_call_sid}_retry", audio_bytes)
        response.play(audio_url)
        response.record(
            action=f"{BASE_URL}/voice/respond?call_sid={effective_call_sid}",
            method="POST",
            max_length=30,
            timeout=5,
            finish_on_key="#",
            play_beep=False,
        )
        return Response(content=str(response), media_type="application/xml")

    try:
        # 1. Transcribir con Whisper
        transcript = await _transcribe_audio(RecordingUrl)
        logger.info(f"Transcript Whisper: '{transcript}'")

        if not transcript:
            raise ValueError("Transcripción vacía")

        # 2. Obtener/crear chat
        chat_id = _get_or_create_voice_chat(effective_call_sid)

        # 3. Guardar mensaje usuario
        repo.save_message(chat_id, "user", transcript)
        save_message_to_memory(VOICE_SYSTEM_USER, "user", transcript)

        # 4. Generar respuesta con GPT-4o
        reply = await _generate_reply_gpt(transcript, chat_id)
        logger.info(f"Respuesta GPT-4o: '{reply}'")

        # 5. Guardar respuesta
        repo.save_message(chat_id, "assistant", reply)
        save_message_to_memory(VOICE_SYSTEM_USER, "assistant", reply)

        # 6. Sintetizar con Nova y guardar en disco
        audio_key = f"{effective_call_sid}_{RecordingSid}"
        audio_bytes = await _synthesize_speech(reply)
        audio_url = _save_audio(audio_key, audio_bytes)

        # 7. Construir TwiML
        response = VoiceResponse()
        response.play(audio_url)
        response.record(
            action=f"{BASE_URL}/voice/respond?call_sid={effective_call_sid}",
            method="POST",
            max_length=30,
            timeout=5,
            finish_on_key="#",
            play_beep=False,
        )
        # Si no habla, colgar
        response.say("Gracias por contactar a Livre. ¡Hasta luego!", language="es-AR")
        response.hangup()

        return Response(content=str(response), media_type="application/xml")

    except Exception as e:
        logger.exception(f"Error procesando respuesta de voz: {e}")
        return Response(content=build_error_response(), media_type="application/xml")


# ---------------------------------------------------------------------------
# Llamada saliente
# ---------------------------------------------------------------------------

class OutboundCallRequest(BaseModel):
    to: str


@router.post("/call")
async def make_call(body: OutboundCallRequest):
    """Origina una llamada saliente al número indicado."""
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
