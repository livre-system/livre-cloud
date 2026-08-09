from app.core.config import GOOGLE_CLIENT_ID  # fuerza load_dotenv
import os
import json
import httpx
from typing import Iterator
from app.llm.provider import LLMProvider


def _load_api_keys() -> list[str]:
    keys = []
    for i in range(1, 10):
        key = os.getenv(f"GEMINI_API_KEY_{i}")
        if key:
            keys.append(key)
    single = os.getenv("GEMINI_API_KEY")
    if single and single not in keys:
        keys.append(single)
    return keys


def _build_body(messages: list[dict]) -> dict:
    contents = [
        {
            "role": "user" if m["role"] == "user" else "model",
            "parts": [{"text": m["content"]}],
        }
        for m in messages
        if m["role"] != "system"
    ]
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    body = {"contents": contents}
    if system_parts:
        body["system_instruction"] = {"parts": [{"text": system_parts[0]}]}
    return body


class GeminiProvider(LLMProvider):

    def __init__(self):
        self.keys = _load_api_keys()
        self.model = os.getenv("LLM_MODEL", "gemini-3.1-flash-lite")
        if not self.keys:
            raise RuntimeError("No hay claves de Gemini definidas en las variables de entorno")

    def generate(self, messages: list[dict]) -> str:
        body = _build_body(messages)
        last_error = None
        for key in self.keys:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent?key={key}"
            )
            try:
                response = httpx.post(url, json=body, timeout=60)
                if response.status_code in (429, 503):
                    last_error = response.text
                    continue
                response.raise_for_status()
                data = response.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except httpx.HTTPStatusError as e:
                last_error = str(e)
                continue

        raise RuntimeError(f"Todas las claves fallaron. Último error: {last_error}")

    def generate_stream(self, messages: list[dict]) -> Iterator[str]:
        body = _build_body(messages)
        last_error = None
        for key in self.keys:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:streamGenerateContent?alt=sse&key={key}"
            )
            try:
                with httpx.stream("POST", url, json=body, timeout=60) as response:
                    if response.status_code in (429, 503):
                        last_error = f"HTTP {response.status_code}"
                        continue
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        chunk = json.loads(line[6:])
                        try:
                            text = chunk["candidates"][0]["content"]["parts"][0]["text"]
                            yield text
                        except (KeyError, IndexError):
                            continue
                return  # éxito, salir del loop de keys
            except httpx.HTTPStatusError as e:
                last_error = str(e)
                continue

        raise RuntimeError(f"Todas las claves fallaron. Último error: {last_error}")
