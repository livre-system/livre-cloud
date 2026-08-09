from app.llm.provider import LLMProvider


class OpenAIProvider(LLMProvider):

    def generate(self, messages: list[dict]) -> str:
        raise NotImplementedError("OpenAI: pendiente de implementación")
