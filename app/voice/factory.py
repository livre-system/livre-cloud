import os
from app.voice.provider import VoiceProvider


def get_voice_provider(provider: str | None = None) -> VoiceProvider:
    provider = (provider or os.getenv("VOICE_PROVIDER", "fish")).lower()

    if provider == "fish":
        from app.voice.fish import FishProvider
        return FishProvider()
    else:
        raise ValueError(f"Proveedor de voz no soportado: {provider}")
