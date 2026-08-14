"""
app/voice/router.py

Endpoints para el flujo de llamadas entrantes de Twilio.

Twilio webhooks:
  POST /voice/incoming   → primera respuesta (saludo + Gather)
  POST /voice/respond    → recibe SpeechResult → procesa → responde

Estos endpoints NO requieren JWT porque Twilio los llama directamente.
La validación se hace con la firma de Twilio (X-Twilio-Signature).
"""

import os
import logging
from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from app.voice.twilio_handler import (
    build_gather_response,
    build_reply_response,
    build_error_response,
)
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

# Usuario de sistema para llamadas (las llamadas se guardan bajo este user_id)
# Podés mapear por número de teléfono del llamante en el futuro.
VOICE_SYSTEM_USER = "voice_calls"


def _get_or_create_voice_chat(call_sid: str) -> str:
    """
    Cada llamada (CallSid) tiene su propio chat.
    Si ya existe, lo reutiliza. Si no, lo crea.
    """
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


def _generate_reply(transcript: str, chat_id: str) -> str:
    """Genera la respuesta del LLM dado el transcript del usuario."""
    # Traer historial del chat para contexto
    history = repo.get_messages(chat_id, VOICE_SYSTEM_USER)

    system_prompt = _build_voice_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history[-10:]]  # últimos 10
    messages.append({"role": "user", "content": transcript})

    llm = get_llm_provider()
    try:
        return llm.generate(messages)
    except Exception:
        # Fallback a Groq
        try:
            groq = GroqProvider()
            return groq.generate(messages)
        except Exception as e:
            logger.error(f"Error generando respuesta de voz: {e}")
            return "Hubo un problema procesando tu consulta. Por favor intentá de nuevo."


@router.post("/incoming")
async def incoming_call(
    request: Request,
    CallSid: str = Form(default=""),
    From: str = Form(default=""),
):
    """
    Webhook inicial de Twilio cuando entra una llamada.
    Responde con TwiML que saluda y activa el Gather para escuchar.
    """
    logger.info(f"Llamada entrante - CallSid: {CallSid}, From: {From}")

    action_url = f"{BASE_URL}/voice/respond?call_sid={CallSid}"
    twiml = build_gather_response(action_url=action_url)

    return Response(content=twiml, media_type="application/xml")


@router.post("/respond")
async def respond_to_speech(
    request: Request,
    SpeechResult: str = Form(default=""),
    Confidence: str = Form(default="0"),
    call_sid: str = "",
    CallSid: str = Form(default=""),
):
    """
    Webhook que recibe el texto transcripto por Twilio (SpeechResult).
    Genera respuesta con el LLM y responde con TwiML.
    """
    # call_sid puede venir como query param o como form field
    effective_call_sid = call_sid or CallSid
    transcript = SpeechResult.strip()

    logger.info(f"Transcript recibido - CallSid: {effective_call_sid}, texto: '{transcript}', confianza: {Confidence}")

    if not transcript:
        action_url = f"{BASE_URL}/voice/respond?call_sid={effective_call_sid}"
        twiml = build_gather_response(
            action_url=action_url,
            say_text="No escuché bien. ¿Podés repetir?",
        )
        return Response(content=twiml, media_type="application/xml")

    try:
        # Obtener o crear el chat de esta llamada
        chat_id = _get_or_create_voice_chat(effective_call_sid)

        # Guardar mensaje del usuario
        repo.save_message(chat_id, "user", transcript)
        save_message_to_memory(VOICE_SYSTEM_USER, "user", transcript)

        # Generar respuesta
        reply = _generate_reply(transcript, chat_id)

        # Guardar respuesta del asistente
        repo.save_message(chat_id, "assistant", reply)
        save_message_to_memory(VOICE_SYSTEM_USER, "assistant", reply)

        # Construir TwiML con la respuesta y volver a escuchar
        action_url = f"{BASE_URL}/voice/respond?call_sid={effective_call_sid}"
        twiml = build_reply_response(reply_text=reply, action_url=action_url)

        return Response(content=twiml, media_type="application/xml")

    except Exception as e:
        logger.exception(f"Error procesando respuesta de voz: {e}")
        twiml = build_error_response()
        return Response(content=twiml, media_type="application/xml")
