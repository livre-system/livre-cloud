"""
app/voice/telnyx.py

Integración Telnyx Media Streams + ElevenLabs Conversational AI.

Arquitectura:
  Telnyx → WebSocket → backend → ElevenLabs → WebSocket → backend → Telnyx

Audio flow verificado contra documentación oficial:
  - Telnyx INBOUND:  base64-encoded RTP payload SIN cabecera (doc: "no headers")
                     Codec = PCMU 8kHz
  - Telnyx OUTBOUND: JSON { event:"media", media:{ payload: base64(RTP) } }
                     RTP con cabecera, codec PCMU 8kHz
  - ElevenLabs manda/recibe PCM 16-bit 16kHz en base64

Llamadas salientes:
  Call Control API: POST /v2/calls con stream_url + stream_bidirectional_mode=rtp
  El streaming arranca automáticamente al contestar. Sin TeXML.

Llamadas entrantes:
  Telnyx llama al webhook /incoming → respondemos TeXML <Connect><Stream>
  Esto es obligatorio para llamadas entrantes en Telnyx.
"""

import os
import re
import json
import base64
import struct
import audioop
import logging
import asyncio
import time
import httpx
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.voice.address_matching import (
    search_top3_streets,      # switch embedding ↔ fuzzy vive en address_matching.py
    search_streets_fuzzy,     # fuzzy-only: importado por claridad; el switch está
                              # en search_top3_streets() dentro de address_matching.py
    is_yes,
    is_no,
    normalize_numbers_in_text,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice-telnyx", tags=["voice-telnyx"])

# ---------------------------------------------------------------------------
# Variables de entorno
# ---------------------------------------------------------------------------
TELNYX_API_KEY       = os.getenv("TELNYX_API_KEY", "")
TELNYX_CONNECTION_ID = os.getenv("TELNYX_CONNECTION_ID", "")  # Call Control App ID
TELNYX_PHONE_NUMBER  = os.getenv("TELNYX_PHONE_NUMBER", "")
BASE_URL             = os.getenv("BASE_URL", "")

# ElevenLabs — mismos valores que elevenlabs.py, no se tocan
ELEVENLABS_API_KEY   = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID  = "agent_3701m0dfvnmmemptqcm2jym9wdvv"
ELEVENLABS_VOICE_ID  = os.getenv("ELEVENLABS_VOICE_ID", "2KrcT8wE5IyKnWz2nknz")

# ---------------------------------------------------------------------------
# ⚠️ MODO DE PRUEBA — bypass de address_matching (AISLAMIENTO DE ASR)
# ---------------------------------------------------------------------------
# Cuando TELNYX_ADDRESS_MATCH_BYPASS=true, _handle_user_transcript_for_address()
# no ejecuta NINGUNA lógica de matching de calles: no llama a
# search_top3_streets()/search_streets_fuzzy() (RapidFuzz, extracción de
# segmento, thresholds), no arma candidatos, y NO manda ningún
# "contextual_update" con una calle sugerida. El transcript de ElevenLabs
# sigue su curso normal hacia el LLM de ElevenLabs sin ninguna corrección
# de nuestro backend.
#
# No afecta: audio, RTP, codec, WebSocket, ElevenLabs, TTS, watchdog,
# timeouts, ni detección de contestador/IVR (_is_voicemail_ivr sigue activa).
#
# REVERSIÓN: poner la env var en "false" o eliminarla. No se borró ni
# modificó ninguna función de app/voice/address_matching.py.
ADDRESS_MATCH_BYPASS = os.getenv("TELNYX_ADDRESS_MATCH_BYPASS", "false").strip().lower() == "true"
if ADDRESS_MATCH_BYPASS:
    logger.warning(
        "[TELNYX] ⚠️  MODO DE PRUEBA ACTIVO — address_matching BYPASSEADO "
        "(TELNYX_ADDRESS_MATCH_BYPASS=true). El transcript de ElevenLabs NO "
        "será corregido por RapidFuzz antes de llegar al LLM."
    )

# ---------------------------------------------------------------------------
# Timeouts de llamada — configurables desde Railway
# ---------------------------------------------------------------------------

# Segundos de inactividad antes de cerrar la llamada.
# "Inactividad" significa silencio completo: sin audio inbound con energía detectable
# y sin user_transcript. Un humano pensando (con ruido de fondo) no dispara este timeout.
# La detección de voicemail/IVR (sobre transcripts) actúa independientemente y más rápido.
IDLE_TIMEOUT_SECS = int(os.getenv("TELNYX_IDLE_TIMEOUT_SECONDS", "25"))

# Duración máxima absoluta de una llamada en segundos.
MAX_CALL_SECS = int(os.getenv("TELNYX_MAX_CALL_SECONDS", "180"))

# ---------------------------------------------------------------------------
# Audio helpers
#
# Documentación oficial confirmada:
# https://developers.telnyx.com/docs/voice/programmable-voice/media-streaming
#
#   RECIBIR de Telnyx (inbound del teléfono):
#     "The payload contains a base64-encoded RTP payload (no headers)."
#     → base64decode directo. Ya es audio PCMU 8kHz puro, sin cabecera RTP.
#     CORRECCIÓN respecto a versión anterior: NO hay que stripear 12 bytes.
#
#   ENVIAR a Telnyx (bidirectional rtp mode):
#     "your base64 encoded RTP stream"
#     → hay que armar paquete RTP completo con cabecera (12 bytes) + payload.
# ---------------------------------------------------------------------------

_rtp_seq       = 0
_rtp_timestamp = 0
_rtp_ssrc      = 0xCAFEBABE


def _pcmu8k_to_pcm16k_b64(pcmu_bytes: bytes) -> str | None:
    """
    PCMU 8kHz (ulaw, sin cabecera) → PCM 16-bit little-endian 16kHz → base64.
    Esto es lo que ElevenLabs espera como user_audio_chunk.
    """
    try:
        pcm8k  = audioop.ulaw2lin(pcmu_bytes, 2)
        pcm16k, _ = audioop.ratecv(pcm8k, 2, 1, 8000, 16000, None)
        return base64.b64encode(pcm16k).decode()
    except Exception as e:
        logger.error(f"[TELNYX] Error PCMU→PCM16k: {e}")
        return None


def _build_rtp_pcmu(pcm16k_bytes: bytes) -> str:
    """
    PCM 16kHz little-endian (de ElevenLabs) → PCMU 8kHz → paquete RTP → base64.
    Telnyx espera RTP completo (con cabecera) en modo bidirectional rtp.
    PT=0 = PCMU según RFC 3551.
    """
    global _rtp_seq, _rtp_timestamp

    # Convertir PCM16k → PCMU 8kHz
    pcm8k, _ = audioop.ratecv(pcm16k_bytes, 2, 1, 16000, 8000, None)
    pcmu     = audioop.lin2ulaw(pcm8k, 2)

    _rtp_seq       = (_rtp_seq + 1) & 0xFFFF
    _rtp_timestamp = (_rtp_timestamp + len(pcmu)) & 0xFFFFFFFF

    # Cabecera RTP mínima 12 bytes
    # V=2, P=0, X=0, CC=0 → 0x80
    # M=0, PT=0 (PCMU)    → 0x00
    header = struct.pack(
        "!BBHII",
        0x80,
        0x00,           # PT=0 = PCMU
        _rtp_seq,
        _rtp_timestamp,
        _rtp_ssrc,
    )
    return base64.b64encode(header + pcmu).decode()


# ---------------------------------------------------------------------------
# TeXML para llamadas ENTRANTES
# ---------------------------------------------------------------------------

def _build_incoming_texml(ws_url: str) -> str:
    """
    TeXML mínimo para llamadas entrantes.
    Abre el WebSocket bidireccional con PCMU 8kHz en ambas direcciones.
    bidirectionalMode=rtp es obligatorio para audio en tiempo real.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream
      url="{ws_url}"
      track="both_tracks"
      bidirectionalMode="rtp"
      bidirectionalCodec="PCMU"
      bidirectionalSamplingRate="8000"
    />
  </Connect>
</Response>"""


# ---------------------------------------------------------------------------
# Validación de direcciones (calle + altura) sobre el transcript de usuario
#
# Reutiliza la búsqueda por embedding de app/voice/address_matching.py (la
# misma que usa el flujo Twilio). No toca audio ni el WebSocket de Telnyx:
# solo lee el texto que ya llega en el evento "user_transcript" y le manda a
# ElevenLabs un "contextual_update" con la calle candidata para que el
# agente la confirme con el usuario en su propia voz.
#
# IMPORTANTE (ver aclaración al final de la respuesta): esto asume que el
# system prompt del agente de ElevenLabs sabe qué hacer con ese contexto
# (preguntar "¿Es <candidata>?"). El backend no puede forzar literalmente
# lo que el agente dice, solo puede informarle.
# ---------------------------------------------------------------------------

async def _send_context_to_eleven(eleven_ws, text: str):
    try:
        await eleven_ws.send(json.dumps({
            "type": "contextual_update",
            "text": text,
        }))
        logger.info(f"[TELNYX] dirección — contexto enviado a ElevenLabs: {text}")
    except Exception as e:
        logger.error(f"[TELNYX] Error enviando contextual_update: {e}")


async def _handle_user_transcript_for_address(text: str, eleven_ws, call_state: dict):
    """
    call_state es un dict propio de la llamada (vive en media_stream) con:
      - "pending": {"candidates": [...], "index": int, "number": str} | None
      - "confirmed_address": str | None
    """
    if not text:
        return

    # --- MODO DE PRUEBA: bypass total del matching de calles -------------
    # Ver "⚠️ MODO DE PRUEBA" arriba en este archivo. No se toca call_state,
    # no se llama a RapidFuzz/address_matching, no se manda contextual_update.
    if ADDRESS_MATCH_BYPASS:
        logger.info(
            f"[TELNYX][TEST-MODE] address_matching bypasseado — "
            f"transcript sin tocar: '{text}'"
        )
        return

    pending = call_state.get("pending")

    try:
        if pending:
            candidates = pending["candidates"]
            index = pending["index"]
            number = pending["number"]

            if is_yes(text):
                direccion = f"{candidates[index]} {number}".strip()
                call_state["pending"] = None
                call_state["confirmed_address"] = direccion
                logger.info(f"[TELNYX] dirección confirmada: {direccion}")
                await _send_context_to_eleven(
                    eleven_ws, f"[DIRECCIÓN CONFIRMADA: {direccion}]"
                )
                return

            if is_no(text) and index + 1 < len(candidates):
                next_index = index + 1
                call_state["pending"]["index"] = next_index
                siguiente = f"{candidates[next_index]} {number}".strip()
                logger.info(f"[TELNYX] dirección — ofreciendo siguiente candidata: {siguiente}")
                await _send_context_to_eleven(
                    eleven_ws,
                    f"[CANDIDATA DE DIRECCIÓN A CONFIRMAR: {siguiente}] "
                    f"Preguntale al usuario, en una frase corta: ¿Es {siguiente}?",
                )
                return

            # Ni sí ni no reconocido, o se acabaron las candidatas: soltamos
            # la detección y dejamos que el agente siga la conversación normal.
            call_state["pending"] = None
            return

        # ---- Detección de una dirección nueva en el transcript ----
        normalized = normalize_numbers_in_text(text)
        numbers = re.findall(r'\b\d+\b', normalized)
        number = numbers[0] if numbers else ""

        # Si hay una calle pendiente de altura del turno anterior, combinamos.
        pending_street_candidates = call_state.get("pending_street")
        if number and pending_street_candidates:
            # Usuario dijo la altura después de haber dicho la calle sola.
            call_state["pending_street"] = None
            call_state["pending"] = {
                "candidates": pending_street_candidates,
                "index": 0,
                "number": number,
            }
            candidata = f"{pending_street_candidates[0]} {number}".strip()
            logger.info(f"[TELNYX] dirección — calle previa + altura nueva: {candidata}")
            await _send_context_to_eleven(
                eleven_ws,
                f"[CANDIDATA DE DIRECCIÓN A CONFIRMAR: {candidata}] "
                f"Preguntale al usuario, en una frase corta: ¿Es {candidata}?",
            )
            return

        candidates = await asyncio.to_thread(search_top3_streets, normalized)
        if not candidates:
            # Sin coincidencia razonable → el agente sigue y pide aclaración
            return

        if not number:
            # Calle detectada pero sin altura todavía: guardar y esperar.
            call_state["pending_street"] = candidates
            logger.info(
                f"[TELNYX] dirección — calle sin altura, guardando candidatas: {candidates}"
            )
            return

        call_state["pending"] = {"candidates": candidates, "index": 0, "number": number}
        call_state["pending_street"] = None
        candidata = f"{candidates[0]} {number}".strip()
        logger.info(f"[TELNYX] dirección — candidata detectada: {candidata}")
        await _send_context_to_eleven(
            eleven_ws,
            f"[CANDIDATA DE DIRECCIÓN A CONFIRMAR: {candidata}] "
            f"Preguntale al usuario, en una frase corta: ¿Es {candidata}?",
        )

    except Exception as e:
        # Cualquier falla acá jamás debe cortar la llamada ni el audio.
        logger.error(f"[TELNYX] Error validando dirección: {e}")


# ---------------------------------------------------------------------------
# Detección de contestador automático / IVR
#
# Opera exclusivamente sobre el user_transcript que ya entrega ElevenLabs.
# No analiza audio, no llama a OpenAI, no usa embeddings.
#
# Estrategia de dos niveles:
#   NIVEL 1 — señales inequívocas (una sola es suficiente):
#     frases que SOLO aparecen en grabaciones o IVR, nunca en conversación humana.
#   NIVEL 2 — señales de refuerzo (se activan con 2 o más, o combinadas con nivel 1):
#     palabras que podrían usarse en conversación humana pero son comunes en
#     contestadores. Se exige combinación para evitar falsos positivos.
#
# Regla de transcript largo: si el primer transcript supera 120 caracteres sin
# respuesta humana previa, casi siempre es un mensaje grabado. Se activa solo
# cuando call_state["human_turns"] == 0 para no cortar a un humano verborrágico.
# ---------------------------------------------------------------------------

# Frases de nivel 1: inequívocas, una sola dispara el corte.
_VOICEMAIL_STRONG: list[tuple[str, str]] = [
    # menú IVR con dígitos
    (r"presion[eéa][e ]?\s*[el]?\s*[12345678]",   "ivr-menu-presione"),
    (r"marc[aáe][e ]?\s*[el]?\s*[12345678]",       "ivr-menu-marque"),
    (r"para dejar\s+(un\s+)?mensaje",               "voicemail-dejar-mensaje"),
    (r"dej[eé]\s+(un\s+)?mensaje",                  "voicemail-deje-mensaje"),
    (r"despu[eé]s\s+de\s+la\s+se[ñn]al",           "voicemail-despues-señal"),
    (r"al\s+o[íi]r\s+la\s+se[ñn]al",               "voicemail-al-oir-señal"),
    (r"buz[oó]n\s+de\s+voz",                        "voicemail-buzon"),
    (r"correo\s+de\s+voz",                          "voicemail-correo"),
    (r"mensaje\s+de\s+voz",                         "voicemail-mensaje-voz"),
    (r"para\s+hablar\s+con\s+un\s+operador",        "ivr-operador"),
    (r"para\s+hablar\s+con\s+un\s+agente",          "ivr-agente"),
    (r"marque\s+cero\s+para",                       "ivr-marque-cero"),
    (r"presione\s+cero\s+para",                     "ivr-presione-cero"),
    (r"men[uú]\s+principal",                        "ivr-menu-principal"),
    (r"opciones\s+del\s+men[uú]",                   "ivr-opciones-menu"),
]

# Frases de nivel 2: requieren al menos 2 coincidencias para disparar el corte.
_VOICEMAIL_SOFT: list[tuple[str, str]] = [
    (r"no\s+(est[aá]\s+)?disponible",               "soft-no-disponible"),
    (r"en\s+este\s+momento\s+no\s+(podemos|puedo)", "soft-en-este-momento"),
    (r"no\s+podemos\s+atender",                     "soft-no-podemos-atender"),
    (r"comuníquese\s+(con|al)",                     "soft-comuniquese"),
    (r"llame\s+(m[aá]s\s+tarde|nuevamente)",        "soft-llame-mas-tarde"),
    (r"intente\s+(m[aá]s\s+tarde|nuevamente)",      "soft-intente-mas-tarde"),
    (r"\bgrabaci[oó]n\b",                           "soft-grabacion"),
    (r"mensaje\s+grabado",                          "soft-mensaje-grabado"),
    (r"el\s+n[uú]mero\s+(marcado|llamado)",         "soft-numero-marcado"),
    (r"fuera\s+del\s+horario",                      "soft-fuera-horario"),
    (r"horario\s+de\s+atenci[oó]n",                 "soft-horario-atencion"),
    (r"para\s+continuar\s+en\s+espa[ñn]ol",        "soft-idioma"),
    (r"seleccione\s+una\s+opci[oó]n",               "soft-seleccione-opcion"),
]

_VOICEMAIL_LONG_TRANSCRIPT_CHARS = 120   # umbral para primer transcript largo

# ---------------------------------------------------------------------------
# Umbral de energía RMS para detección de audio activo
#
# Se aplica sobre PCM 16-bit (resultado de audioop.ulaw2lin) antes del
# upsampling. Un valor de 200 captura voz susurrada y ruido de fondo suave;
# silencio de línea telefónica suele estar por debajo de 50.
# No distingue humano de contestador — eso lo hace _is_voicemail_ivr().
# ---------------------------------------------------------------------------
_AUDIO_ENERGY_THRESHOLD = 200   # RMS mínimo sobre PCM 16-bit para considerar audio activo


def _is_voicemail_ivr(text: str, human_turns: int) -> tuple[bool, str]:
    """
    Analiza un user_transcript y devuelve (es_contestador, motivo).

    Parámetros:
      text        — transcript recibido de ElevenLabs (puede ser voz del contestador).
      human_turns — cuántos transcripts previos ya se procesaron en esta llamada.
                    Si es 0, es el primer turno; se aplica la regla de longitud.

    Retorna:
      (True, motivo)  → contestador/IVR detectado, cerrar llamada.
      (False, "")     → parece humano, continuar normal.
    """
    if not text:
        return False, ""

    normalized = text.lower()

    # Nivel 1: una sola coincidencia fuerte alcanza.
    for pattern, label in _VOICEMAIL_STRONG:
        if re.search(pattern, normalized):
            return True, label

    # Nivel 2: necesitamos al menos 2 coincidencias suaves.
    soft_hits: list[str] = []
    for pattern, label in _VOICEMAIL_SOFT:
        if re.search(pattern, normalized):
            soft_hits.append(label)
    if len(soft_hits) >= 2:
        return True, "+".join(soft_hits)

    # Regla de longitud: primer transcript muy largo → probable mensaje grabado.
    if human_turns == 0 and len(text) >= _VOICEMAIL_LONG_TRANSCRIPT_CHARS:
        return True, f"long-transcript-{len(text)}chars"

    return False, ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/incoming")
async def incoming_call(request: Request):
    """
    Webhook para llamadas entrantes.
    Configurar en Telnyx: Portal → tu número → Voice URL → esta URL.
    """
    host   = BASE_URL.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{host}/voice-telnyx/stream"
    logger.info(f"[TELNYX] call/control — llamada entrante → {ws_url}")
    return Response(content=_build_incoming_texml(ws_url), media_type="application/xml")


@router.websocket("/stream")
async def media_stream(websocket: WebSocket):
    """
    WebSocket bidireccional Telnyx ↔ ElevenLabs.

    INBOUND (teléfono → ElevenLabs):
      Telnyx manda: { event:"media", media:{ track:"inbound", payload:"<b64 PCMU 8kHz sin RTP header>" } }
      → decode b64 → PCMU bytes → convertir a PCM 16kHz → ElevenLabs

    OUTBOUND (ElevenLabs → teléfono):
      ElevenLabs manda: PCM 16kHz little-endian en base64
      → decodificar → convertir a PCMU 8kHz → armar RTP con cabecera → Telnyx
    """
    await websocket.accept()
    logger.info("[TELNYX] conexión — WebSocket aceptado")

    eleven_ws    = None
    eleven_ready = asyncio.Event()
    stop_event   = asyncio.Event()   # señaliza cierre a ambos loops
    user_spoke   = asyncio.Event()   # se setea en cada user_transcript confirmado humano
    audio_active = asyncio.Event()   # se setea cuando llega audio inbound con energía detectable
    call_state: dict = {
        "pending": None,
        "confirmed_address": None,
        "pending_street": None,
        "human_turns": 0,   # transcripts procesados como humanos (para regla longitud)
    }

    logger.info(
        f"[TELNYX] llamada iniciada — idle_timeout={IDLE_TIMEOUT_SECS}s "
        f"max_call={MAX_CALL_SECS}s"
    )

    async def _close_eleven(reason: str = "stop"):
        """Envía close_socket a ElevenLabs y cierra el WebSocket limpiamente."""
        ws = eleven_ws
        if ws is None:
            return
        logger.info(f"[ELEVEN] cerrando conversación (razón: {reason})")
        try:
            if ws.open:
                await ws.send(json.dumps({"type": "close_socket"}))
        except Exception:
            pass  # ya estaba cerrado, ignorar
        try:
            await ws.close()
        except Exception:
            pass
        logger.info("[ELEVEN] WebSocket cerrado")

    try:
        # Conectar a ElevenLabs
        eleven_ws = await websockets.connect(
            f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={ELEVENLABS_AGENT_ID}",
            additional_headers={"xi-api-key": ELEVENLABS_API_KEY},
        )
        logger.info("[ELEVEN] conexión — WebSocket conectado")

        # Configuración de inicio de conversación.
        # voice_id en tts override: fuerza la voz aunque el agente tenga otra
        # configurada en el dashboard. Fuente: documentación oficial ElevenLabs
        # https://elevenlabs.io/docs/eleven-agents/api-reference/eleven-agents/websocket
        initiation_payload: dict = {
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {
                "agent": {"language": "es"},
                "tts":   {"optimize_streaming_latency": 3},
            },
        }
        if ELEVENLABS_VOICE_ID:
            initiation_payload["conversation_config_override"]["tts"]["voice_id"] = ELEVENLABS_VOICE_ID
            logger.info(f"[ELEVEN] voz override activo — voice_id={ELEVENLABS_VOICE_ID}")
        else:
            logger.warning("[ELEVEN] ELEVENLABS_VOICE_ID no configurado — se usará la voz del agente en el dashboard")
        await eleven_ws.send(json.dumps(initiation_payload))

        # ------------------------------------------------------------------
        # LOOP 1: Telnyx → ElevenLabs
        # ------------------------------------------------------------------
        async def telnyx_to_eleven():
            audio_chunks = 0
            try:
                async for raw_msg in websocket.iter_text():
                    data  = json.loads(raw_msg)
                    event = data.get("event")

                    if event == "connected":
                        logger.info("[TELNYX] call/control — connected")

                    elif event == "start":
                        fmt    = data.get("start", {}).get("media_format", {})
                        caller = data.get("start", {}).get("from", "?")
                        sid    = data.get("stream_id", "?")
                        logger.info(f"[TELNYX] call/control — start stream_id={sid} from={caller} format={fmt}")

                    elif event == "media":
                        await eleven_ready.wait()

                        track       = data.get("media", {}).get("track", "inbound")
                        payload_b64 = data.get("media", {}).get("payload", "")

                        # Ignorar outbound (es lo que nosotros mandamos)
                        if track == "outbound" or not payload_b64:
                            continue

                        audio_chunks += 1
                        if audio_chunks % 100 == 0:
                            logger.info(f"[TELNYX] audio recibido — {audio_chunks} chunks")

                        # Payload = PCMU 8kHz puro, SIN cabecera RTP (confirmado en doc)
                        try:
                            pcmu_bytes = base64.b64decode(payload_b64)
                        except Exception as e:
                            logger.error(f"[TELNYX] Error decode payload: {e}")
                            continue

                        # Señalizar actividad de audio al watchdog si la energía supera
                        # el umbral. Distingue silencio de línea de voz/ruido real.
                        # No distingue humano de contestador — eso lo hace _is_voicemail_ivr().
                        try:
                            pcm_raw = audioop.ulaw2lin(pcmu_bytes, 2)
                            if audioop.rms(pcm_raw, 2) >= _AUDIO_ENERGY_THRESHOLD:
                                audio_active.set()
                        except Exception:
                            pass  # nunca interrumpir el flujo de audio por esto

                        # PCMU 8kHz → PCM 16kHz → ElevenLabs
                        pcm16k_b64 = _pcmu8k_to_pcm16k_b64(pcmu_bytes)
                        if pcm16k_b64 is None:
                            continue

                        try:
                            await eleven_ws.send(json.dumps({"user_audio_chunk": pcm16k_b64}))
                            if audio_chunks % 100 == 0:
                                logger.info(f"[ELEVEN] audio enviado — {audio_chunks} chunks")
                        except Exception as e:
                            logger.error(f"[TELNYX→ELEVEN] Error: {e}")
                            break

                    elif event == "stop":
                        logger.info(f"[TELNYX] stop recibido — total audio: {audio_chunks} chunks")
                        stop_event.set()
                        await _close_eleven(reason="telnyx-stop")
                        break

                    elif event == "dtmf":
                        logger.info(f"[TELNYX] call/control — DTMF: {data.get('dtmf', {}).get('digit')}")

                    elif event == "error":
                        logger.error(f"[TELNYX] call/control — error: {data}")

                    else:
                        logger.debug(f"[TELNYX] call/control — evento: {event}")

            except WebSocketDisconnect:
                logger.info("[TELNYX] conexión — desconectado (disconnect abrupto)")
                stop_event.set()
                await _close_eleven(reason="telnyx-disconnect")

        # ------------------------------------------------------------------
        # LOOP 2: ElevenLabs → Telnyx
        # ------------------------------------------------------------------
        async def eleven_to_telnyx():
            try:
                async for raw_msg in eleven_ws:
                    # Si ya se señalizó el cierre, descartar mensajes restantes
                    # y dejar que el loop termine naturalmente.
                    if stop_event.is_set():
                        break

                    data       = json.loads(raw_msg)
                    event_type = data.get("type")

                    if event_type == "conversation_initiation_metadata":
                        meta    = data.get("conversation_initiation_metadata_event", {})
                        conv_id = meta.get("conversation_id", "?")
                        out_fmt = meta.get("agent_output_audio_format", "?")
                        in_fmt  = meta.get("user_input_audio_format", "?")
                        logger.info(f"[ELEVEN] conexión — sesión iniciada id={conv_id} entrada={in_fmt} salida={out_fmt}")
                        eleven_ready.set()

                    elif event_type == "audio":
                        audio_b64 = data.get("audio_event", {}).get("audio_base_64", "")
                        if not audio_b64:
                            continue
                        try:
                            pcm16k_bytes = base64.b64decode(audio_b64)
                            rtp_b64      = _build_rtp_pcmu(pcm16k_bytes)
                            await websocket.send_text(json.dumps({
                                "event": "media",
                                "media": {"payload": rtp_b64},
                            }))
                            logger.debug("[TELNYX] audio enviado — chunk PCMU RTP")
                        except Exception as e:
                            logger.error(f"[ELEVEN→TELNYX] Error: {e}")

                    elif event_type == "interruption":
                        logger.info("[ELEVEN] audio enviado — interrupción, clear buffer")
                        try:
                            await websocket.send_text(json.dumps({"event": "clear"}))
                        except Exception:
                            pass

                    elif event_type == "ping":
                        event_id = data.get("ping_event", {}).get("event_id")
                        if event_id:
                            await eleven_ws.send(json.dumps({"type": "pong", "event_id": event_id}))

                    elif event_type == "agent_response":
                        text = data.get("agent_response_event", {}).get("agent_response", "")
                        logger.info(f"[ELEVEN GPT] '{text}'")

                    elif event_type == "user_transcript":
                        text = data.get("user_transcription_event", {}).get("user_transcript", "")
                        logger.info(f"[ELEVEN USUARIO] '{text}'")

                        # --------------------------------------------------
                        # Detección de contestador / IVR
                        # Se ejecuta ANTES de user_spoke.set() para que un
                        # mensaje grabado no reinicie el idle timer.
                        # --------------------------------------------------
                        is_vm, vm_reason = _is_voicemail_ivr(
                            text, call_state["human_turns"]
                        )
                        if is_vm:
                            logger.warning(
                                f"[TELNYX] contestador/IVR detectado — cerrando llamada "
                                f"(señal: {vm_reason}, texto: '{text[:80]}')"
                            )
                            logger.info("[TELNYX] llamada finalizada — reason=voicemail-ivr")
                            stop_event.set()
                            await _close_eleven(reason="voicemail-ivr")
                            break

                        # Humano real: registrar turno y continuar normal.
                        call_state["human_turns"] += 1
                        # Señalizar actividad humana real al watchdog de inactividad.
                        user_spoke.set()
                        await _handle_user_transcript_for_address(text, eleven_ws, call_state)

                    elif event_type == "error":
                        logger.error(f"[ELEVEN ERROR] {data}")

                    else:
                        logger.debug(f"[ELEVEN] evento: {event_type}")

            except Exception as e:
                if not stop_event.is_set():
                    # Solo loguear si NO fue un cierre intencional nuestro
                    logger.error(f"[ELEVEN] eleven_to_telnyx error: {e}")

        # ------------------------------------------------------------------
        # WATCHDOG: timeout de inactividad + timeout absoluto
        # Corre en paralelo con los dos loops de audio.
        # ------------------------------------------------------------------
        async def _watchdog():
            """
            Dos capas de protección:
              1. Timeout absoluto: MAX_CALL_SECS desde el arranque.
              2. Timeout de inactividad: IDLE_TIMEOUT_SECS sin ninguna señal de vida.

            Señales de vida (cualquiera resetea el idle timer):
              - audio_active: llega audio inbound con energía > threshold (humano O contestador).
              - user_spoke:   llegó un user_transcript confirmado como no-voicemail.

            La detección de voicemail/IVR actúa en eleven_to_telnyx() independientemente
            y con prioridad — corta antes de que el watchdog llegue a evaluar el idle.

            Esto permite:
              - humano hablando → audio_active setea, no corta
              - humano pensando con ruido → audio_active setea, no corta
              - silencio completo → nada setea → corta por idle (contestador mudo,
                llamada abandonada, o timeout de espera inicial)
              - contestador con voz → audio_active setea, pero _is_voicemail_ivr()
                corta inmediatamente al llegar el primer transcript
            """
            deadline = time.monotonic() + MAX_CALL_SECS

            while not stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        f"[TELNYX] timeout absoluto — cerrando llamada "
                        f"(max={MAX_CALL_SECS}s)"
                    )
                    logger.info("[TELNYX] llamada finalizada — reason=max-call-timeout")
                    stop_event.set()
                    await _close_eleven(reason="max-call-timeout")
                    return

                # Resetear ambas señales y esperar la primera que llegue.
                # Se resetean juntas para evitar que una señal previa enmascare
                # un período de silencio posterior.
                audio_active.clear()
                user_spoke.clear()
                wait_secs = min(IDLE_TIMEOUT_SECS, remaining)

                # Esperar audio_active OR user_spoke, lo que llegue primero.
                done, _ = await asyncio.wait(
                    [
                        asyncio.ensure_future(asyncio.shield(audio_active.wait())),
                        asyncio.ensure_future(asyncio.shield(user_spoke.wait())),
                    ],
                    timeout=wait_secs,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if done:
                    # Al menos una señal llegó: reiniciar el idle timer.
                    continue

                # Ninguna señal en IDLE_TIMEOUT_SECS → silencio completo.
                if stop_event.is_set():
                    return
                logger.warning(
                    f"[TELNYX] timeout inactividad — cerrando llamada "
                    f"(idle={IDLE_TIMEOUT_SECS}s sin audio ni transcript)"
                )
                logger.info("[TELNYX] llamada finalizada — reason=idle-timeout")
                stop_event.set()
                await _close_eleven(reason="idle-timeout")
                return

        await asyncio.gather(telnyx_to_eleven(), eleven_to_telnyx(), _watchdog())

    except Exception as e:
        logger.exception(f"[TELNYX] Error en media_stream: {e}")
    finally:
        # Garantía final: si por alguna razón _close_eleven no se llamó antes
        # (excepción inesperada en gather), cerramos igual.
        if not stop_event.is_set():
            await _close_eleven(reason="finally-fallback")
        logger.info("[TELNYX] conexión — sesión finalizada")


# ---------------------------------------------------------------------------
# Llamada SALIENTE — Call Control API, sin TeXML
#
# Doc: https://developers.telnyx.com/api/call-control/dial-call
# Endpoint: POST https://api.telnyx.com/v2/calls
#
# stream_url + stream_bidirectional_mode=rtp arranca el WebSocket
# automáticamente al contestar la llamada. No hace falta webhook adicional.
# ---------------------------------------------------------------------------

class OutboundCallRequest(BaseModel):
    to: str


@router.post("/call")
async def make_call(body: OutboundCallRequest):
    """
    Llamada saliente via Call Control API de Telnyx.

    Variables requeridas en Railway:
      TELNYX_API_KEY       → KEY... (Portal → Auth → API Keys)
      TELNYX_CONNECTION_ID → ID de la Call Control App (no TeXML App)
      TELNYX_PHONE_NUMBER  → número Telnyx E.164 (+1...)
      BASE_URL             → https://livre-cloud-production.up.railway.app
    """
    if not TELNYX_API_KEY or not TELNYX_CONNECTION_ID or not TELNYX_PHONE_NUMBER:
        raise HTTPException(
            status_code=500,
            detail="Faltan: TELNYX_API_KEY, TELNYX_CONNECTION_ID o TELNYX_PHONE_NUMBER.",
        )

    host   = BASE_URL.replace("https://", "").replace("http://", "")
    ws_url = f"wss://{host}/voice-telnyx/stream"

    payload = {
        "to":            body.to,
        "from":          TELNYX_PHONE_NUMBER,
        "connection_id": TELNYX_CONNECTION_ID,
        # Streaming bidireccional arranca al contestar
        "stream_url":                         ws_url,
        "stream_track":                       "both_tracks",
        "stream_bidirectional_mode":          "rtp",
        "stream_bidirectional_codec":         "PCMU",
        "stream_bidirectional_sampling_rate": 8000,
    }

    logger.info(f"[TELNYX] call/control — llamada saliente to={body.to} stream={ws_url}")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.telnyx.com/v2/calls",
                headers={
                    "Authorization": f"Bearer {TELNYX_API_KEY}",
                    "Content-Type":  "application/json",
                    "Accept":        "application/json",
                },
                json=payload,
            )

        if resp.status_code not in (200, 201, 202):
            logger.error(f"[TELNYX] Error saliente {resp.status_code}: {resp.text[:400]}")
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Telnyx {resp.status_code}: {resp.text[:400]}",
            )

        data            = resp.json().get("data", {})
        call_control_id = data.get("call_control_id", "?")
        logger.info(f"[TELNYX] call/control — iniciada call_control_id={call_control_id}")

        return {
            "call_control_id": call_control_id,
            "call_leg_id":     data.get("call_leg_id"),
            "to":              body.to,
            "from":            TELNYX_PHONE_NUMBER,
            "stream_url":      ws_url,
        }

    except httpx.RequestError as e:
        logger.exception(f"[TELNYX] Error de red: {e}")
        raise HTTPException(status_code=502, detail=f"Error de red: {e}")
