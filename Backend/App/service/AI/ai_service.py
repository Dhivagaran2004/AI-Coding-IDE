import asyncio
from collections.abc import AsyncIterator
from contextvars import ContextVar

from App.config import (
    AI_MAX_RETRIES,
    AI_REQUEST_TIMEOUT_SECONDS,
    AI_RETRY_BASE_DELAY,
)
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
        fallback_provider: BaseLLMProvider | None = None,
        max_retries: int = AI_MAX_RETRIES,
        retry_base_delay: float = AI_RETRY_BASE_DELAY,
        request_timeout_seconds: float = AI_REQUEST_TIMEOUT_SECONDS,
    ):
        if (
            max_retries < 0
            or max_retries > 10
            or retry_base_delay < 0
            or request_timeout_seconds <= 0
        ):
            raise ValueError("AI retry settings are outside the supported bounds.")
        self.provider = provider
        self.fallback_provider = fallback_provider
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.request_timeout_seconds = request_timeout_seconds
        self._current_provider: ContextVar[BaseLLMProvider | None] = ContextVar(
            f"ai_service_provider_{id(self)}",
            default=provider,
        )

    @property
    def current_provider(self) -> BaseLLMProvider | None:
        return self._current_provider.get()

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

    @staticmethod
    def _is_rate_limited(error: Exception) -> bool:
        return getattr(error, "status_code", None) in (402, 429)

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

        return await self._generate_with_fallback(
            "generate",
            message=message,
            context=context,
            history=history,
        )

    async def _generate_from_provider(
        self,
        provider: BaseLLMProvider,
        method_name: str,
        message: str,
        context: str | None,
        history: list[dict[str, str]] | None,
    ) -> str:
        generate = getattr(provider, method_name, None)
        if not callable(generate):
            generate = provider.generate

        for attempt in range(self.max_retries + 1):
            try:
                return await asyncio.wait_for(
                    generate(
                        message=message,
                        context=context,
                        history=history,
                    ),
                    timeout=self.request_timeout_seconds,
                )
            except Exception as exc:
                if self._is_rate_limited(exc):
                    raise
                if attempt >= self.max_retries or not self._is_retryable(exc):
                    raise
                await self._wait_before_retry(attempt + 1)
        raise RuntimeError("AI retry loop ended unexpectedly.")

    async def _generate_with_fallback(
        self,
        method_name: str,
        message: str,
        context: str | None,
        history: list[dict[str, str]] | None,
    ) -> str:
        if self.provider is None:
            raise RuntimeError("AI provider is not configured.")

        active_provider = self.current_provider
        if active_provider not in (self.provider, self.fallback_provider):
            active_provider = self.provider
        alternate_provider = (
            self.fallback_provider
            if active_provider is self.provider
            else self.provider
        )
        self._current_provider.set(active_provider)
        try:
            return await self._generate_from_provider(
                active_provider,
                method_name,
                message,
                context,
                history,
            )
        except Exception as exc:
            if (
                alternate_provider is None
                or not self._is_rate_limited(exc)
            ):
                raise

        self._current_provider.set(alternate_provider)
        return await self._generate_from_provider(
            alternate_provider,
            method_name,
            message,
            context,
            history,
        )

    async def chat_json(
        self,
        message: str,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """Request a JSON object when the provider supports JSON mode."""
        return await self._generate_with_fallback(
            "generate_json",
            message=message,
            context=context,
            history=history,
        )

    async def _stream_from_provider(
        self,
        provider: BaseLLMProvider,
        message: str,
        context: str | None,
        history: list[dict[str, str]] | None,
    ) -> AsyncIterator[str]:
        for attempt in range(self.max_retries + 1):
            emitted = False
            try:
                async for chunk in provider.generate_stream(
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
                    or self._is_rate_limited(exc)
                    or attempt >= self.max_retries
                    or not self._is_retryable(exc)
                ):
                    raise
                await self._wait_before_retry(attempt + 1)
        raise RuntimeError("AI stream retry loop ended unexpectedly.")

    async def stream(
        self,
        message: str,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        """Stream a chat response through the configured provider."""
        if self.provider is None:
            raise RuntimeError("AI provider is not configured.")

        active_provider = self.current_provider
        if active_provider not in (self.provider, self.fallback_provider):
            active_provider = self.provider
        alternate_provider = (
            self.fallback_provider
            if active_provider is self.provider
            else self.provider
        )
        self._current_provider.set(active_provider)
        emitted = False
        try:
            async for chunk in self._stream_from_provider(
                active_provider,
                message,
                context,
                history,
            ):
                if chunk:
                    emitted = True
                    yield chunk
        except Exception as exc:
            if (
                emitted
                or alternate_provider is None
                or not self._is_rate_limited(exc)
            ):
                raise

            self._current_provider.set(alternate_provider)
            async for chunk in self._stream_from_provider(
                alternate_provider,
                message,
                context,
                history,
            ):
                if chunk:
                    yield chunk