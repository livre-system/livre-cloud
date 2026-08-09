from app.llm.provider import LLMProvider


class OllamaProvider(LLMProvider):

    def generate(self, messages: list[dict]) -> str:
        raise NotImplementedError("Ollama: pendiente de implementación")
