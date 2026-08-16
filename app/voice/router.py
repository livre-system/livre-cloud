"""
app/voice/router.py
"""

import os
import re
import logging
import subprocess
import tempfile
import httpx
from fastapi import APIRouter, Form, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import VoiceResponse
from openai import AsyncOpenAI

from app.core.config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
from app.memory.embeddings import get_embedding
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

# Estado por llamada: candidatos de calle pendientes de confirmación
# { call_sid: { "candidates": [...], "index": 0, "number": "500" } }
_pending_confirmation: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Audio: boost de volumen con ffmpeg
# ---------------------------------------------------------------------------

def _boost_audio(audio_bytes: bytes) -> bytes:
    """Aumenta el volumen del audio 3x con ffmpeg para mejorar transcripción."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as inp:
            inp.write(audio_bytes)
            inp_path = inp.name
        out_path = inp_path.replace(".mp3", "_boosted.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-i", inp_path, "-filter:a", "volume=3.0", out_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        with open(out_path, "rb") as f:
            result = f.read()
        os.unlink(inp_path)
        os.unlink(out_path)
        return result
    except Exception as e:
        logger.warning(f"ffmpeg boost falló, usando audio original: {e}")
        return audio_bytes


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
        "- Respondé en máximo 2 oraciones cortas.\n"
        "- SIEMPRE respondé en español argentino, sin excepción.\n\n"
        "FLUJO DE PEDIDO DE VIAJE:\n"
        "- Pedí primero el punto de origen (¿Desde dónde salís?).\n"
        "- Luego pedí el destino (¿A dónde vas?).\n"
        "- Cuando el sistema confirme una dirección te la pasará como [DIRECCIÓN CONFIRMADA: ...]. Usala para confirmar con el usuario.\n"
        "- Confirmá origen y destino en voz alta antes de cerrar.\n"
        "- No ofrezcas otros canales si el usuario ya está en la llamada.\n"
    )


# ---------------------------------------------------------------------------
# Embeddings: top 3 candidatos
# ---------------------------------------------------------------------------

def _search_top3_streets(query: str) -> list[str]:
    street_part = re.sub(r'\b\d+\b', '', query).strip()
    if not street_part:
        return []
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
            return [r["name"] for r in rows if r["similarity"] > 0.5]
    except Exception as e:
        logger.warning(f"Error buscando calles top3: {e}")
    return []


# ---------------------------------------------------------------------------
# Detección de sí/no
# ---------------------------------------------------------------------------

def _is_yes(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ["sí", "si", "correcto", "exacto", "eso", "ese", "esa", "ok", "dale", "claro"])


def _is_no(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ["no", "negativo", "otro", "otra", "distinto"])


# ---------------------------------------------------------------------------
# Generación de respuesta GPT
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# TwiML con velocidad 1.25x via SSML
# ---------------------------------------------------------------------------

def _build_say_and_record_twiml(action_url: str, say_text: str) -> str:
    response = VoiceResponse()
    ssml = f'<speak><prosody rate="fast">{say_text}</prosody></speak>'
    response.say(ssml, language="es-AR", voice="Google.es-US-Neural2-A")
    response.record(
        action=action_url,
        method="POST",
        max_length=10,
        timeout=3,
        play_beep=False,
        trim="trim-silence",
    )
    return str(response)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/incoming")
async def incoming_call(
    request: Request,
    CallSid: str = Form(default=""),
    From: str = Form(default=""),
):
    logger.info(f"Llamada entrante - CallSid: {CallSid}, From: {From}")
    action_url = f"{BASE_URL}/voice/recording?call_sid={CallSid}"
    twiml = _build_say_and_record_twiml(action_url, "Hola, soy LIVRE IA. ¿En qué te puedo ayudar?")
    return Response(content=twiml, media_type="application/xml")


@router.post("/recording")
async def handle_recording(
    request: Request,
    RecordingUrl: str = Form(default=""),
    call_sid: str = "",
    CallSid: str = Form(default=""),
):
    effective_call_sid = call_sid or CallSid
    action_url = f"{BASE_URL}/voice/recording?call_sid={effective_call_sid}"

    if not RecordingUrl:
        twiml = _build_say_and_record_twiml(action_url, "No escuché bien. ¿Podés repetir?")
        return Response(content=twiml, media_type="application/xml")

    try:
        # Descargar audio
        audio_url = RecordingUrl + ".mp3"
        async with httpx.AsyncClient(timeout=15) as client:
            audio_resp = await client.get(audio_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
            audio_bytes = audio_resp.content

        # Boost de volumen
        audio_bytes = _boost_audio(audio_bytes)

        # Transcribir con Groq Whisper
        groq = GroqProvider()
        transcript = groq.transcribe(audio_bytes, filename="audio.mp3")
        logger.info(f"Whisper transcript: '{transcript}'")

        if not transcript:
            twiml = _build_say_and_record_twiml(action_url, "No escuché bien. ¿Podés repetir?")
            return Response(content=twiml, media_type="application/xml")

        chat_id = _get_or_create_voice_chat(effective_call_sid)

        # ---- MODO CONFIRMACIÓN: usuario responde sí/no a una calle ----
        if effective_call_sid in _pending_confirmation:
            pending = _pending_confirmation[effective_call_sid]
            candidates = pending["candidates"]
            index = pending["index"]
            number = pending["number"]

            if _is_yes(transcript):
                chosen = f"{candidates[index]} {number}".strip()
                del _pending_confirmation[effective_call_sid]
                enriched = f"{transcript} [DIRECCIÓN CONFIRMADA: {chosen}]"
                logger.info(f"Dirección confirmada: {chosen}")

            elif _is_no(transcript) and index + 1 < len(candidates):
                # Pasar al siguiente candidato
                next_index = index + 1
                _pending_confirmation[effective_call_sid]["index"] = next_index
                next_calle = candidates[next_index]
                reply = f"¿Es {next_calle} {number}?".strip()
                repo.save_message(chat_id, "user", transcript)
                repo.save_message(chat_id, "assistant", reply)
                twiml = _build_say_and_record_twiml(action_url, reply)
                return Response(content=twiml, media_type="application/xml")

            else:
                # No hay más candidatos o no se entendió
                del _pending_confirmation[effective_call_sid]
                enriched = transcript

        else:
            # ---- MODO NORMAL: buscar candidatos ----
            candidates = _search_top3_streets(transcript)
            numbers = re.findall(r'\b\d+\b', transcript)
            number = numbers[0] if numbers else ""

            if candidates:
                # Preguntar por el primero
                _pending_confirmation[effective_call_sid] = {
                    "candidates": candidates,
                    "index": 0,
                    "number": number,
                }
                reply = f"¿Es {candidates[0]} {number}?".strip()
                repo.save_message(chat_id, "user", transcript)
                repo.save_message(chat_id, "assistant", reply)
                twiml = _build_say_and_record_twiml(action_url, reply)
                return Response(content=twiml, media_type="application/xml")
            else:
                enriched = transcript

        repo.save_message(chat_id, "user", transcript)
        save_message_to_memory(VOICE_SYSTEM_USER, "user", transcript)

        reply = await _generate_reply_gpt(enriched, chat_id)
        logger.info(f"Respuesta GPT: '{reply}'")

        repo.save_message(chat_id, "assistant", reply)
        save_message_to_memory(VOICE_SYSTEM_USER, "assistant", reply)

        twiml = _build_say_and_record_twiml(action_url, reply)
        return Response(content=twiml, media_type="application/xml")

    except Exception as e:
        logger.exception(f"Error procesando recording: {e}")
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
