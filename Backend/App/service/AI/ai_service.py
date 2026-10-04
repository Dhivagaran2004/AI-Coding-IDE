import asyncio
from collections.abc import AsyncIterator

from App.config import AI_MAX_RETRIES, AI_RETRY_BASE_DELAY
from App.service.AI.providers.base_provider import BaseLLMProvider


class AIService:
    """
    Core AI service.

    Handles application-level AI logic while keeping
    the actual LLM implementation inside a provider.
    """

    def __init__(
        self,
        provider: BaseLLMProvider | None = None,
        max_retries: int = AI_MAX_RETRIES,
        retry_base_delay: float = AI_RETRY_BASE_DELAY,
    ):
        if max_retries < 0 or max_retries > 10 or retry_base_delay < 0:
            raise ValueError("AI retry settings are outside the supported bounds.")
        self.provider = provider
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        if isinstance(error, (TimeoutError, ConnectionError, OSError)):
            return True

        status_code = getattr(error, "status_code", None)
        if isinstance(status_code, int):
            return status_code in (408, 425, 429) or 500 <= status_code <= 599

        return type(error).__name__ in {
            "APIConnectionError",
            "APITimeoutError",
            "InternalServerError",
            "RateLimitError",
        }

    async def _wait_before_retry(self, retry_number: int) -> None:
        delay = min(self.retry_base_delay * (2 ** (retry_number - 1)), 2.0)
        if delay:
            await asyncio.sleep(delay)

    async def chat(
        self,
        message: str,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """
        Send a chat request through the configured LLM provider.
        """

        if self.provider is None:
            raise RuntimeError(
                "AI provider is not configured."
            )

        for attempt in range(self.max_retries + 1):
            try:
                return await self.provider.generate(
                    message=message,
                    context=context,
                    history=history,
                )
            except Exception as exc:
                if attempt >= self.max_retries or not self._is_retryable(exc):
                    raise
                await self._wait_before_retry(attempt + 1)
        raise RuntimeError("AI retry loop ended unexpectedly.")

    async def stream(
        self,
        message: str,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        """Stream a chat response through the configured provider."""
        if self.provider is None:
            raise RuntimeError("AI provider is not configured.")

        for attempt in range(self.max_retries + 1):
            emitted = False
            try:
                async for chunk in self.provider.generate_stream(
                    message=message,
                    context=context,
                    history=history,
                ):
                    if chunk:
                        emitted = True
                        yield chunk
                return
            except Exception as exc:
                if (
                    emitted
                    or attempt >= self.max_retries
                    or not self._is_retryable(exc)
                ):
                    raise
                await self._wait_before_retry(attempt + 1)