import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from App.api import ai_router
from App.auth.auth import get_current_user
from App.database.database import get_db
from App.service.AI.ai_service import AIService
from App.service.AI.providers.base_provider import BaseLLMProvider


class StreamingAIService:
    def __init__(self, chunks=None, error=None):
        self.chunks = chunks or ["first ", "second"]
        self.error = error

    async def stream(self, message, context=None, history=None):
        if self.error:
            raise self.error
        for chunk in self.chunks:
            yield chunk

    async def chat(self, message, context=None, history=None):
        if self.error:
            raise self.error
        return "".join(self.chunks)


class ProviderStatusError(RuntimeError):
    def __init__(self, status_code):
        super().__init__("private provider response and token")
        self.status_code = status_code


def make_client(monkeypatch, service):
    app = FastAPI()

    async def override_db():
        yield object()

    async def override_user():
        return type("UserIdentity", (), {"id": 1})()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    monkeypatch.setattr(ai_router, "ai_service", service)
    app.include_router(ai_router.router)
    return TestClient(app)


def test_stream_endpoint_emits_chunks_once_and_final_response(monkeypatch):
    client = make_client(monkeypatch, StreamingAIService())

    response = client.post(
        "/ai/chat/stream",
        json={"message": "Explain this function"},
    )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["content"] for event in events[:-1]] == [
        "first ",
        "second",
    ]
    assert events[-1]["type"] == "done"
    assert events[-1]["response"]["message"] == "first second"


def test_stream_endpoint_returns_safe_error_event(monkeypatch):
    client = make_client(
        monkeypatch,
        StreamingAIService(error=RuntimeError("private provider URL and token")),
    )

    response = client.post(
        "/ai/chat/stream",
        json={"message": "Explain this function"},
    )

    assert response.status_code == 200
    event = json.loads(
        next(line.removeprefix("data: ") for line in response.text.splitlines()
             if line.startswith("data: "))
    )
    assert event == {
        "type": "error",
        "detail": "AI provider is temporarily unavailable. Try again.",
    }
    assert "private provider URL" not in response.text


@pytest.mark.parametrize(
    ("status_code", "expected_detail"),
    [
        (
            401,
            "AI provider authentication failed (HTTP 401). "
            "Check GROQ_API_KEY and provider access.",
        ),
        (
            404,
            "Configured Groq model was not found (HTTP 404). "
            "Check GROQ_MODEL.",
        ),
        (
            429,
            "Groq rate limit or quota was reached (HTTP 429). "
            "Check your Groq plan and try again later.",
        ),
        (
            402,
            "Groq rejected the request because of a billing or quota issue "
            "(HTTP 402). Check your Groq account limits.",
        ),
        (
            408,
            "Groq request timed out. Try again.",
        ),
    ],
)
def test_stream_endpoint_returns_safe_provider_status_hint(
    monkeypatch,
    status_code,
    expected_detail,
):
    client = make_client(
        monkeypatch,
        StreamingAIService(error=ProviderStatusError(status_code)),
    )

    response = client.post(
        "/ai/chat/stream",
        json={"message": "Explain this code"},
    )
    event = json.loads(
        next(
            line.removeprefix("data: ")
            for line in response.text.splitlines()
            if line.startswith("data: ")
        )
    )

    assert response.status_code == 200
    assert event["detail"] == expected_detail
    assert "private provider response" not in response.text
    assert "token" not in response.text

    response = client.post(
        "/ai/chat",
        json={"message": "Explain this code"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == expected_detail
    assert "private provider response" not in response.text
    assert "token" not in response.text


class LegacyProvider(BaseLLMProvider):
    async def generate(self, message, context=None, history=None):
        return "complete response"


def test_legacy_provider_streams_generated_response_as_single_chunk():
    async def collect():
        return [
            chunk async for chunk in LegacyProvider().generate_stream("hello")
        ]

    assert asyncio.run(collect()) == ["complete response"]


def test_ai_json_request_has_a_total_timeout():
    class HangingProvider:
        def __init__(self):
            self.calls = 0

        async def generate_json(self, **_kwargs):
            self.calls += 1
            await asyncio.Event().wait()

    provider = HangingProvider()
    service = AIService(
        provider=provider,
        max_retries=0,
        request_timeout_seconds=0.01,
    )

    with pytest.raises(TimeoutError):
        asyncio.run(service.chat_json("Return a plan"))

    assert provider.calls == 1
