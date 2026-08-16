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
        "- SIEMPRE respondé en español argentino, sin excepción.\n"
        "- Nunca respondas en inglés ni en otro idioma.\n\n"
        "FLUJO DE PEDIDO DE VIAJE:\n"
        "- Si el usuario quiere solicitar un viaje, tomá los datos vos mismo por teléfono. No lo mandés a la app ni a WhatsApp.\n"
        "- Pedí primero el punto de origen (¿Desde dónde salís?).\n"
        "- Luego pedí el destino (¿A dónde vas?).\n"
        "- Cuando el usuario dé una dirección, el sistema ya la validó con Google Maps y te la pasará entre corchetes como [DIRECCIÓN VALIDADA EN MAPS: ...]. Usá ese dato para confirmar.\n"
        "- Si el sistema NO incluye [DIRECCIÓN VALIDADA EN MAPS: ...], significa que no se encontró la dirección. En ese caso decí exactamente lo que escuchaste y preguntá si está bien. Ejemplo: 'Escuché Vierte 500, ¿es correcto? ¿Podés repetirlo más despacio o deletrear la calle?'\n"
        "- NUNCA inventes ni asumas una dirección si no viene validada. Si no la encontraste, decilo.\n"
        "- Siempre repetí lo que escuchaste antes de confirmar. Ejemplo: 'Entendí que salís desde Vieytes 500, ¿es correcto?'\n"
        "- Confirmá origen y destino en voz alta antes de cerrar.\n"
        "- No ofrezcas otros canales si el usuario ya está en la llamada.\n"
    )



def _search_street_by_embedding(query: str) -> str | None:
    """Busca la calle más similar en la DB usando embeddings."""
    embedding = get_embedding(query)
    if not embedding:
        return None
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT name, 1 - (embedding <=> %s::vector) AS similarity
                FROM street_embeddings
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT 1
            """, (str(embedding), str(embedding)))
            row = cur.fetchone()
            if row and row["similarity"] > 0.75:
                logger.info(f"Calle encontrada por embedding: '{row['name']}' (similarity={row['similarity']:.2f})")
                return row["name"]
    except Exception as e:
        logger.warning(f"Error buscando calle por embedding: {e}")
    return None


async def _correct_address_phonetic(address: str) -> str:
    """Usa GPT para corregir errores fonéticos de Twilio antes de buscar en Maps."""
    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sos un corrector de direcciones de Bahía Blanca, Buenos Aires, Argentina. "
                        "Twilio transcribe llamadas telefónicas con errores fonéticos. "
                        "Tu tarea es corregir el texto recibido a una dirección real de Bahía Blanca. "
                        "Ejemplos de errores comunes: 'Vierte' → 'Vieytes', 'Donato' → 'Donado', "
                        "'Urquisa' → 'Urquiza', 'Ayen' → 'Alem'. "
                        "Respondé SOLO con la dirección corregida, sin explicación. "
                        "Si no parece una dirección, respondé exactamente con el texto original."
                    ),
                },
                {"role": "user", "content": address},
            ],
            max_tokens=50,
            temperature=0,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"No se pudo corregir fonéticamente la dirección: {e}")
        return address


async def _validate_address(address: str) -> str | None:
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        return None

    # Paso 1: buscar calle por embedding (más rápido y preciso para errores fonéticos)
    import re
    street_from_embedding = _search_street_by_embedding(address)
    if street_from_embedding:
        numbers = re.findall(r'\b\d+\b', address)
        number = numbers[0] if numbers else ""
        corrected = f"{street_from_embedding} {number}".strip()
        logger.info(f"Calle corregida por embedding: '{address}' → '{corrected}'")
    else:
        # Fallback: corregir errores fonéticos con GPT
        corrected = await _correct_address_phonetic(address)
        logger.info(f"Dirección original: '{address}' → corregida por GPT: '{corrected}'")

    # Paso 2: buscar con Places Text Search (más tolerante que Geocoding)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={
                "query": f"{corrected}, Bahía Blanca, Buenos Aires, Argentina",
                "key": api_key,
                "language": "es",
                "region": "ar",
            },
        )
        data = resp.json()
        logger.info(f"Places API status: {data.get('status')} para '{corrected}'")

        if data.get("status") == "OK" and data.get("results"):
            result = data["results"][0]
            formatted = result.get("formatted_address", "")
            # Verificar que el resultado sea de Bahía Blanca
            if "Bahía Blanca" in formatted or "Bahia Blanca" in formatted:
                # Retornar solo la parte relevante (sin país)
                parts = formatted.split(",")
                if len(parts) >= 2:
                    return f"{parts[0].strip()}, {parts[1].strip()}"
                return parts[0].strip()

    return None

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


def _get_street_hints() -> str:
    """Carga los nombres de calles de la DB para pasarlos como hints a Twilio."""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT name FROM street_embeddings ORDER BY name")
            rows = cur.fetchall()
            return ", ".join(r["name"] for r in rows)
    except Exception as e:
        logger.warning(f"No se pudieron cargar hints de calles: {e}")
        return ""

_STREET_HINTS = None

def _get_cached_hints() -> str:
    global _STREET_HINTS
    if _STREET_HINTS is None:
        _STREET_HINTS = _get_street_hints()
    return _STREET_HINTS


def _build_gather_twiml(action_url: str, say_text: str) -> str:
    response = VoiceResponse()
    hints = _get_cached_hints()
    gather = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language="es-AR",
        speech_timeout="auto",
        timeout=8,
        speech_model="phone_call",
        enhanced=True,
        hints=hints if hints else None,
    )
    gather.say(say_text, language="es-AR", voice="Google.es-US-Neural2-A")
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
    action_url = f"{BASE_URL}/voice/respond?call_sid={CallSid}"
    twiml = _build_gather_twiml(action_url, "Hola, soy LIVRE IA. ¿En qué te puedo ayudar?")
    return Response(content=twiml, media_type="application/xml")


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
        twiml = _build_gather_twiml(action_url, "No escuché bien. ¿Podés repetir?")
        return Response(content=twiml, media_type="application/xml")

    try:
        chat_id = _get_or_create_voice_chat(effective_call_sid)
        repo.save_message(chat_id, "user", transcript)
        save_message_to_memory(VOICE_SYSTEM_USER, "user", transcript)

        # Intentar validar si el transcript parece una dirección
        validated = await _validate_address(transcript)
        if validated:
            enriched = f"{transcript} [DIRECCIÓN VALIDADA EN MAPS: {validated}]"
        else:
            enriched = transcript

        reply = await _generate_reply_gpt(enriched, chat_id)
        logger.info(f"Respuesta GPT-4o: '{reply}'")

        repo.save_message(chat_id, "assistant", reply)
        save_message_to_memory(VOICE_SYSTEM_USER, "assistant", reply)

        twiml = _build_gather_twiml(action_url, reply)
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
