import asyncio

import pytest

from App.service.AI.providers import provider_factory
from App.service.AI.providers.gemini_provider import GeminiProvider


def test_factory_builds_gemini_provider_from_gemini_settings(monkeypatch):
    monkeypatch.setattr(provider_factory, "GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setattr(provider_factory, "GEMINI_MODEL", "gemini-test-model")

    provider = provider_factory.get_llm_provider(" Gemini ")

    assert isinstance(provider, GeminiProvider)
    assert provider.model == "gemini-test-model"
    assert (
        str(provider.client.base_url).rstrip("/")
        == "https://generativelanguage.googleapis.com/v1beta/openai"
    )
    assert provider.client.max_retries == 0


def test_factory_requires_gemini_api_key_without_exposing_it(monkeypatch):
    monkeypatch.setattr(provider_factory, "GEMINI_API_KEY", "")

    with pytest.raises(ValueError, match="GEMINI_API_KEY is required"):
        provider_factory.get_llm_provider("gemini")


@pytest.mark.parametrize(
    ("primary_name", "groq_key", "gemini_key", "expected_type"),
    [
        ("groq", "groq-key", "gemini-key", GeminiProvider),
        ("gemini", "groq-key", "gemini-key", provider_factory.GroqProvider),
        ("groq", "groq-key", "", type(None)),
        ("gemini", "", "gemini-key", type(None)),
    ],
)
def test_factory_builds_only_configured_alternate_provider(
    monkeypatch,
    primary_name,
    groq_key,
    gemini_key,
    expected_type,
):
    monkeypatch.setattr(provider_factory, "GROQ_API_KEY", groq_key)
    monkeypatch.setattr(provider_factory, "GEMINI_API_KEY", gemini_key)
    monkeypatch.setattr(provider_factory, "GROQ_MODEL", "groq-test-model")
    monkeypatch.setattr(provider_factory, "GEMINI_MODEL", "gemini-test-model")

    fallback = provider_factory.get_fallback_llm_provider(primary_name)

    if expected_type is type(None):
        assert fallback is None
    else:
        assert isinstance(fallback, expected_type)
