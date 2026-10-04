import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from App.api import ai_router
from App.auth.auth import get_current_user
from App.database.database import get_db
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
        return "".join(self.chunks)


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


class LegacyProvider(BaseLLMProvider):
    async def generate(self, message, context=None, history=None):
        return "complete response"


def test_legacy_provider_streams_generated_response_as_single_chunk():
    async def collect():
        return [
            chunk async for chunk in LegacyProvider().generate_stream("hello")
        ]

    assert asyncio.run(collect()) == ["complete response"]
