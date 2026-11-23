from App.service.AI.providers.groq_provider import GroqProvider


class GeminiProvider(GroqProvider):
    """Gemini through Google's OpenAI-compatible chat completions endpoint."""

    provider_name = "gemini"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
    API_KEY_NAME = "GEMINI_API_KEY"
