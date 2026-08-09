import os
import json
import httpx
from typing import Iterator
from app.llm.provider import LLMProvider

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class GroqProvider(LLMProvider):

    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")
        self.model = os.getenv("GROQ_MODEL", "llama3-8b-8192")
        if not self.api_key:
            raise RuntimeError("No hay GROQ_API_KEY definida en las variables de entorno")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def transcribe(self, audio_bytes: bytes, filename: str = "audio.webm") -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": (filename, audio_bytes, "audio/webm")}
        data = {"model": "whisper-large-v3-turbo", "language": "es"}
        response = httpx.post(GROQ_WHISPER_URL, headers=headers, files=files, data=data, timeout=30)
        response.raise_for_status()
        return response.json().get("text", "").strip()

    def generate(self, messages: list[dict]) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        response = httpx.post(GROQ_API_URL, headers=self._headers(), json=body, timeout=60)
        if not response.is_success:
            import logging
            logging.getLogger(__name__).error(f"Groq error body: {response.text}")
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def generate_stream(self, messages: list[dict]) -> Iterator[str]:
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        with httpx.stream("POST", GROQ_API_URL, headers=self._headers(), json=body, timeout=60) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = line[6:]
                if chunk == "[DONE]":
                    return
                try:
                    data = json.loads(chunk)
                    text = data["choices"][0]["delta"].get("content", "")
                    if text:
                        yield text
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue
