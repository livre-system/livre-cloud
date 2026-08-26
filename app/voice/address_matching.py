"""
app/voice/address_matching.py

Lógica compartida de detección y validación de direcciones (calle + altura)
contra la tabla street_embeddings de Bahía Blanca.

Usada por:
  - app/voice/router.py   (flujo Twilio + GPT)
  - app/voice/telnyx.py   (flujo Telnyx + ElevenLabs)

Se extrajo a un módulo aparte a propósito: así ninguno de los dos flujos de
voz duplica esta lógica, y telnyx.py NO necesita importar router.py (que
carga Twilio, Groq y el cliente de chat de OpenAI) solo para reusarla. Este
módulo solo depende de get_embedding() y get_cursor().
"""

import re
import logging

from app.memory.embeddings import get_embedding
from app.core.database import get_cursor

logger = logging.getLogger(__name__)

# cache: texto de calle → top3 calles candidatas
_embedding_cache: dict[str, list[str]] = {}


# ---------------------------------------------------------------------------
# Búsqueda de calles por embedding (igual a la lógica original de router.py)
# ---------------------------------------------------------------------------

def search_top3_streets(query: str) -> list[str]:
    street_part = re.sub(r'\b\d+\b', '', query).strip().lower()
    if not street_part:
        return []

    if street_part in _embedding_cache:
        logger.info(f"Cache hit para '{street_part}'")
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
        logger.warning(f"Error buscando calles top3: {e}")
    return []


# ---------------------------------------------------------------------------
# Detección sí/no
# ---------------------------------------------------------------------------

def is_yes(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ["sí", "si", "correcto", "exacto", "eso", "ese", "esa", "ok", "dale", "claro"])


def is_no(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ["no", "negativo", "otro", "otra", "distinto"])


# ---------------------------------------------------------------------------
# Números dichos en palabras → dígitos ("mil quinientos" → "1500")
#
# Cubre lo típico de una altura de calle (hasta ~9999). No es un parser de
# lenguaje natural completo a propósito: solo lo necesario para direcciones.
# ---------------------------------------------------------------------------

_UNITS = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "dieciséis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintidos": 22,
    "veintidós": 22, "veintitres": 23, "veintitrés": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiseis": 26, "veintiséis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29,
}
_TENS = {
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
}
_HUNDREDS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300,
    "cuatrocientos": 400, "quinientos": 500, "seiscientos": 600,
    "setecientos": 700, "ochocientos": 800, "novecientos": 900,
}
_NUMBER_WORDS = set(_UNITS) | set(_TENS) | set(_HUNDREDS) | {"mil", "y"}


def _words_to_number(words: list[str]) -> int:
    total = 0
    current = 0
    for w in words:
        if w == "y":
            continue
        if w == "mil":
            current = current or 1
            total += current * 1000
            current = 0
        elif w in _HUNDREDS:
            current += _HUNDREDS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w in _UNITS:
            current += _UNITS[w]
    return total + current


def normalize_numbers_in_text(text: str) -> str:
    """
    Convierte números dichos en palabras a dígitos dentro del texto, para
    poder extraerlos después con un regex \\d+ normal.

    "avenida alem mil quinientos" → "avenida alem 1500"
    """
    tokens = text.lower().split()
    out_tokens: list[str] = []
    buffer: list[str] = []

    def flush():
        if buffer:
            out_tokens.append(str(_words_to_number(buffer)))
            buffer.clear()

    for tok in tokens:
        clean = re.sub(r'[^\wáéíóúñ]', '', tok)
        if clean in _NUMBER_WORDS:
            buffer.append(clean)
        else:
            flush()
            out_tokens.append(tok)
    flush()
    return " ".join(out_tokens)
