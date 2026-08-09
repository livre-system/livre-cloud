from app.llm.provider import LLMProvider


class GrokProvider(LLMProvider):

    def generate(self, messages: list[dict]) -> str:
        raise NotImplementedError("Grok: pendiente de implementación")
