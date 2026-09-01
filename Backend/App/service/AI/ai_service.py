from App.service.AI.providers.base_provider import BaseLLMProvider


class AIService:
    """
    Core AI service.

    AIService contains application-level AI logic while
    keeping the actual LLM implementation inside a provider.
    """

    def __init__(self, provider: BaseLLMProvider | None = None):
        self.provider = provider

    async def chat(
        self,
        message: str,
        context: str | None = None,
    ) -> str:
        """
        Send a chat request through the configured LLM provider.
        """

        if self.provider is None:
            return "AI service is not connected to an LLM provider yet."

        return await self.provider.generate(
            message=message,
            context=context,
        )