from abc import ABC, abstractmethod
from typing import Iterator


class LLMProvider(ABC):

    @abstractmethod
    def generate(self, messages: list[dict]) -> str:
        raise NotImplementedError

    @abstractmethod
    def generate_stream(self, messages: list[dict]) -> Iterator[str]:
        raise NotImplementedError
