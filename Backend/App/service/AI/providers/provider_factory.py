from App.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
)

from App.service.AI.providers.base_provider import BaseLLMProvider
from App.service.AI.providers.mock_provider import MockLLMProvider
from App.service.AI.providers.qwen_provider import QwenProvider


def get_llm_provider(provider_name: str) -> BaseLLMProvider:
    """
    Create an LLM provider based on the configured provider name.
    """

    provider = provider_name.lower().strip()

    if provider == "mock":
        return MockLLMProvider()

    if provider == "qwen":
        return QwenProvider(
            model=LLM_MODEL,
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
        )

    raise ValueError(
        f"Unsupported LLM provider: {provider_name}"
    )