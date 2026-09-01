from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    """
    Base interface for all LLM providers.

    Any provider such as OpenAI, Gemini, Anthropic,
    or a local model must implement this interface.
    """

    @abstractmethod
    async def generate(
        self,
        message: str,
        context: str | None = None,
    ) -> str:
        """
        Generate an AI response.

        Args:
            message: User's message.
            context: Optional code or additional context.

        Returns:
            Generated AI response.
        """
        raise NotImplementedError