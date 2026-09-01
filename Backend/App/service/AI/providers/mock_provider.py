from App.service.AI.providers.base_provider import BaseLLMProvider


class MockLLMProvider(BaseLLMProvider):
    """
    Development provider used to test the AI architecture
    without connecting to an external LLM.
    """

    async def generate(
        self,
        message: str,
        context: str | None = None,
    ) -> str:
        return (
            "Mock AI response: "
            f"I received your message: {message}"
        )