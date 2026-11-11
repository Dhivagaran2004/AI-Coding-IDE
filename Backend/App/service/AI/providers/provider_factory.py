from App.config import (
    GROQ_API_KEY,
    GROQ_MODEL,
)

from App.service.AI.providers.base_provider import BaseLLMProvider
from App.service.AI.providers.groq_provider import GroqProvider
from App.service.AI.providers.mock_provider import MockLLMProvider


def get_llm_provider(provider_name: str) -> BaseLLMProvider:
    """
    Create an LLM provider based on the configured provider name.
    """

    provider = provider_name.lower().strip()

    if provider == "mock":
        return MockLLMProvider()

    if provider == "groq":
        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY is required when LLM_PROVIDER=groq.")
        return GroqProvider(model=GROQ_MODEL, api_key=GROQ_API_KEY)

    raise ValueError(
        f"Unsupported LLM provider: {provider_name}"
    )