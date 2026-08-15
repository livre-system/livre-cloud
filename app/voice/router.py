"""
app/voice/router.py
"""

import os
import logging
import httpx
from fastapi import APIRouter, Form, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import AsyncOpenAI

from app.core.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
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

AUDIO_DIR = "/data/uploads/voice_audio"
os.makedirs(AUDIO_DIR, exist_ok=True)


def _save_audio(key: str, audio_bytes: bytes) -> str:
    filename = f"{key}.mp3"
    filepath = os.path.join(AUDIO_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(audio_bytes)
    return f"{BASE_URL}/uploads/voice_audio/{filename}"


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
        "- SIEMPRE respondé en español argentino, sin excepción.\n"
        "- Nunca respondas en inglés ni en otro idioma.\n"
    )


async def _synthesize_speech(text: str) -> bytes:
    # Se leen en cada llamada para tomar los valores actuales de Railway
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "")

    if not api_key or not voice_id:
        logger.error("ELEVENLABS_API_KEY o ELEVENLABS_VOICE_ID no configurados")
        raise ValueError("ElevenLabs no configurado")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "language_code": "es-AR",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.content


async def _generate_reply_gpt(transcript: str, chat_id: str) -> str:
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
        try:
            llm = get_llm_provider()
            return llm.generate(messages)
        except Exception:
            groq = GroqProvider()
            return groq.generate(messages)


def _build_gather_twiml(action_url: str, audio_url: str) -> str:
    response = VoiceResponse()
    gather = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language="es-AR",
        speech_timeout="auto",
        timeout=8,
        speech_model="phone_call",
        enhanced=True,
    )
    gather.play(audio_url)
    response.append(gather)
    response.redirect(action_url, method="POST")
    return str(response)


@router.post("/incoming")
async def incoming_call(
    request: Request,
    CallSid: str = Form(default=""),
    From: str = Form(default=""),
):
    logger.info(f"Llamada entrante - CallSid: {CallSid}, From: {From}")
    try:
        saludo = "Hola, soy LIVRE IA. ¿En qué te puedo ayudar?"
        audio_bytes = await _synthesize_speech(saludo)
        audio_url = _save_audio(f"{CallSid}_welcome", audio_bytes)
        action_url = f"{BASE_URL}/voice/respond?call_sid={CallSid}"
        twiml = _build_gather_twiml(action_url, audio_url)
        return Response(content=twiml, media_type="application/xml")
    except Exception as e:
        logger.exception(f"Error en incoming: {e}")
        response = VoiceResponse()
        response.say("Hubo un problema técnico. Por favor intentá de nuevo.", language="es-AR")
        response.hangup()
        return Response(content=str(response), media_type="application/xml")


@router.post("/respond")
async def respond_to_speech(
    request: Request,
    SpeechResult: str = Form(default=""),
    Confidence: str = Form(default="0"),
    call_sid: str = "",
    CallSid: str = Form(default=""),
):
    effective_call_sid = call_sid or CallSid
    transcript = SpeechResult.strip()
    logger.info(f"Transcript - CallSid: {effective_call_sid}, texto: '{transcript}'")

    action_url = f"{BASE_URL}/voice/respond?call_sid={effective_call_sid}"

    if not transcript:
        try:
            audio_bytes = await _synthesize_speech("No escuché bien. ¿Podés repetir?")
            audio_url = _save_audio(f"{effective_call_sid}_retry", audio_bytes)
            twiml = _build_gather_twiml(action_url, audio_url)
        except Exception:
            response = VoiceResponse()
            response.say("No escuché bien. ¿Podés repetir?", language="es-AR")
            response.redirect(action_url, method="POST")
            twiml = str(response)
        return Response(content=twiml, media_type="application/xml")

    try:
        chat_id = _get_or_create_voice_chat(effective_call_sid)
        repo.save_message(chat_id, "user", transcript)
        save_message_to_memory(VOICE_SYSTEM_USER, "user", transcript)

        reply = await _generate_reply_gpt(transcript, chat_id)
        logger.info(f"Respuesta GPT-4o: '{reply}'")

        repo.save_message(chat_id, "assistant", reply)
        save_message_to_memory(VOICE_SYSTEM_USER, "assistant", reply)

        audio_bytes = await _synthesize_speech(reply)
        audio_url = _save_audio(f"{effective_call_sid}_{len(reply)}", audio_bytes)

        twiml = _build_gather_twiml(action_url, audio_url)
        return Response(content=twiml, media_type="application/xml")

    except Exception as e:
        logger.exception(f"Error procesando respuesta: {e}")
        response = VoiceResponse()
        response.say("Hubo un problema técnico. Por favor intentá de nuevo.", language="es-AR")
        response.hangup()
        return Response(content=str(response), media_type="application/xml")


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
