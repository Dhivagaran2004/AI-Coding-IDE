import asyncio

import pytest

from App.service.AI.ai_service import AIService
from App.service.AI.providers.base_provider import BaseLLMProvider


class TemporaryProviderError(Exception):
    status_code = 503


class RetryProvider(BaseLLMProvider):
    def __init__(self, failures, stream_fail_after_chunk=False):
        self.failures = failures
        self.calls = 0
        self.stream_fail_after_chunk = stream_fail_after_chunk

    async def generate(self, message, context=None, history=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise TemporaryProviderError("temporary provider error")
        return "complete"

    async def generate_stream(self, message, context=None, history=None):
        self.calls += 1
        if self.stream_fail_after_chunk:
            yield "partial"
            raise TemporaryProviderError("interrupted")
        if self.calls <= self.failures:
            raise TemporaryProviderError("temporary provider error")
        yield "complete"


class InvalidKeyProvider(RetryProvider):
    async def generate(self, message, context=None, history=None):
        self.calls += 1
        error = RuntimeError("invalid key")
        error.status_code = 401
        raise error


def test_transient_failures_retry_boundedly():
    provider = RetryProvider(failures=2)
    result = asyncio.run(
        AIService(provider, max_retries=2, retry_base_delay=0).chat("hello")
    )
    assert result == "complete"
    assert provider.calls == 3


def test_authentication_failure_is_not_retried():
    provider = InvalidKeyProvider(failures=0)
    with pytest.raises(RuntimeError, match="invalid key"):
        asyncio.run(
            AIService(provider, max_retries=4, retry_base_delay=0).chat("hello")
        )
    assert provider.calls == 1


def test_stream_retries_before_first_chunk_but_not_after_partial_output():
    pre_chunk_provider = RetryProvider(failures=1)

    async def collect(provider):
        return [
            chunk
            async for chunk in AIService(
                provider,
                max_retries=2,
                retry_base_delay=0,
            ).stream("hello")
        ]

    assert asyncio.run(collect(pre_chunk_provider)) == ["complete"]
    assert pre_chunk_provider.calls == 2

    partial_provider = RetryProvider(
        failures=0,
        stream_fail_after_chunk=True,
    )
    with pytest.raises(TemporaryProviderError):
        asyncio.run(collect(partial_provider))
    assert partial_provider.calls == 1
