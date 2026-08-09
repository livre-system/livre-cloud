from abc import ABC, abstractmethod


class VoiceProvider(ABC):

    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Recibe texto y devuelve audio en bytes (mp3)."""
        ...
