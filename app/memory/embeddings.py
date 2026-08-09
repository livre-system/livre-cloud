import os
import requests
import logging

log = logging.getLogger(__name__)

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/embeddings"
NVIDIA_MODEL = "nvidia/nv-embedqa-e5-v5"


def get_embedding(text: str) -> list[float] | None:
    if not NVIDIA_API_KEY:
        log.error("NVIDIA API key no está definida")
        return None
    if not text or not text.strip():
        return None
    try:
        res = requests.post(
            NVIDIA_URL,
            headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"},
            json={
                "model": NVIDIA_MODEL,
                "input": [text[:8000]],
                "input_type": "passage",
                "encoding_format": "float",
                "truncate": "END"
            },
            timeout=15,
        )
        res.raise_for_status()
        values = res.json()["data"][0]["embedding"]
        log.info(f"Embedding generado OK, dims={len(values)}")
        return values
    except Exception as e:
        log.error(f"Error generando embedding: {e}")
        return None
