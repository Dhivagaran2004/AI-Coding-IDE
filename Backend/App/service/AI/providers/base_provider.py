from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    """
    Base interface for all LLM providers.

    Any provider such as Qwen, OpenAI, Gemini,
    Anthropic, or a local model must implement
    this interface.
    """

    @abstractmethod
    async def generate(
        self,
        message: str,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """
        Generate an AI response.

        Args:
            message: Current user message.
            context: Optional code or additional context.
            history: Previous conversation messages.

        Returns:
            Generated AI response.
        """

        raise NotImplementedError