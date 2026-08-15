import os
import logging
from openai import OpenAI

log = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = "text-embedding-3-small"

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def get_embedding(text: str) -> list[float] | None:
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY no está definida")
        return None
    if not text or not text.strip():
        return None
    try:
        client = _get_client()
        res = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text[:8000],
        )
        values = res.data[0].embedding
        log.info(f"Embedding generado OK, dims={len(values)}")
        return values
    except Exception as e:
        log.error(f"Error generando embedding: {e}")
        return None
