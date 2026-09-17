import os
import logging
import time
from openai import OpenAI

log = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# text-embedding-3-small: modelo estable de OpenAI (desde ene 2024).
# Dimensión: 1536 floats.
# No requiere input_type: el mismo endpoint sirve para indexar y buscar.
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 1536

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY no está definida")
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def get_embedding(text: str, input_type: str = "passage") -> list[float] | None:
    """
    Genera un embedding con OpenAI text-embedding-3-small.

    El parámetro input_type se conserva por compatibilidad con el resto del
    código (repository.py llama get_embedding con input_type="passage" y
    get_query_embedding llama con input_type="query"), pero OpenAI no hace
    distinción asimétrica: ambos tipos producen el mismo vector normalizado.
    No se envía al API.

    Dimensión de salida: 1536.
    """
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY no está definida")
        return None
    if not text or not text.strip():
        return None
    client = _get_client()
    for attempt in range(1, 4):
        try:
            res = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text[:8000],
            )
            values = res.data[0].embedding
            log.info(
                f"Embedding generado OK — model={EMBEDDING_MODEL}, "
                f"dims={len(values)}, input_type={input_type}, attempt={attempt}"
            )
            return values
        except Exception as e:
            if attempt == 3:
                log.error(
                    f"Error generando embedding (input_type={input_type}, "
                    f"attempts={attempt}): {e}",
                    exc_info=True,
                )
                return None
            time.sleep(2 ** (attempt - 1))
    return None


def get_query_embedding(text: str) -> list[float] | None:
    """
    Atajo para embeddings de búsqueda semántica.
    Con OpenAI text-embedding-3-small es idéntico a get_embedding,
    pero se mantiene como función separada para claridad y compatibilidad.
    """
    return get_embedding(text, input_type="query")
