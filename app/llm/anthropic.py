from app.llm.provider import LLMProvider


class AnthropicProvider(LLMProvider):

    def generate(self, messages: list[dict]) -> str:
        raise NotImplementedError("Anthropic: pendiente de implementación")
