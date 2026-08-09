import os
import requests
from app.voice.provider import VoiceProvider

FISH_API_URL = "https://api.fish.audio/v1/tts"


class FishProvider(VoiceProvider):

    def __init__(self):
        self.api_key = os.getenv("FISH_AUDIO_API_KEY")
        self.voice_id = os.getenv("FISH_AUDIO_VOICE_ID")
        if not self.api_key:
            raise RuntimeError("FISH_AUDIO_API_KEY no configurada")
        if not self.voice_id:
            raise RuntimeError("FISH_AUDIO_VOICE_ID no configurada")

    def synthesize(self, text: str, voice_id: str | None = None) -> bytes:
        """Sin streaming — mantener por compatibilidad."""
        response = requests.post(
            FISH_API_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "reference_id": voice_id or self.voice_id,
                "format": "mp3",
                "latency": "low",
                "model": "s2.1-pro-free",
            },
            timeout=30,
        )
        if not response.ok:
            print(f"Fish Audio error {response.status_code}: {response.text}")
            raise RuntimeError(f"Fish Audio error {response.status_code}: {response.text}")
        return response.content

    def synthesize_stream(self, text: str):
        """Genera audio en streaming, yield de chunks binarios."""
        response = requests.post(
            FISH_API_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "reference_id": self.voice_id,
                "format": "mp3",
                "latency": "balanced",
                "streaming": True,
                "model": "s2.1-pro-free",
            },
            timeout=60,
            stream=True,
        )
        if not response.ok:
            print(f"Fish Audio stream error {response.status_code}: {response.text}")
            raise RuntimeError(f"Fish Audio error {response.status_code}: {response.text}")
        for chunk in response.iter_content(chunk_size=4096):
            if chunk:
                yield chunk
