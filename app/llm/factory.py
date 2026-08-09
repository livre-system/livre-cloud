import os
from app.llm.provider import LLMProvider


def get_llm_provider(provider: str | None = None) -> LLMProvider:
    provider = (provider or os.getenv("LLM_PROVIDER", "auto")).lower()

    if provider == "gemini":
        from app.llm.gemini import GeminiProvider
        return GeminiProvider()
    elif provider == "groq":
        from app.llm.groq import GroqProvider
        return GroqProvider()
    elif provider == "openai":
        from app.llm.openai import OpenAIProvider
        return OpenAIProvider()
    elif provider == "anthropic":
        from app.llm.anthropic import AnthropicProvider
        return AnthropicProvider()
    elif provider == "grok":
        from app.llm.grok import GrokProvider
        return GrokProvider()
    elif provider == "ollama":
        from app.llm.ollama import OllamaProvider
        return OllamaProvider()
    elif provider == "auto":
        # Fallback automático: Gemini primero, Groq si falla
        try:
            from app.llm.gemini import GeminiProvider
            return GeminiProvider()
        except Exception:
            from app.llm.groq import GroqProvider
            return GroqProvider()
    else:
        raise ValueError(f"Proveedor LLM no soportado: {provider}")
