from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


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

    async def generate_stream(
        self,
        message: str,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        """Stream a response, falling back to a single chunk for legacy providers."""
        yield await self.generate(message, context=context, history=history)