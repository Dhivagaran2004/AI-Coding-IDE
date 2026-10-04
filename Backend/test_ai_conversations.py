from collections.abc import Generator
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from App.api import ai_router
from App.api.conversation_router import router as conversation_router
from App.auth.auth import get_current_user
from App.database.database import Base, get_db
from App.models.ai_conversation import AIConversation
from App.models.user import User


class FakeAIService:
    def __init__(self):
        self.histories = []

    async def chat(self, message, context=None, history=None):
        self.histories.append(history)
        return f"Response to: {message}"

    async def stream(self, message, context=None, history=None):
        self.histories.append(history)
        yield "Streamed "
        yield f"response to: {message}"


class FailingStreamingAIService(FakeAIService):
    async def stream(self, message, context=None, history=None):
        raise RuntimeError("provider credentials must not be exposed")
        yield ""


@pytest.fixture
def client(monkeypatch) -> Generator[tuple[TestClient, Session], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    test_session = sessionmaker(bind=engine)()
    test_session.add_all(
        [
            User(name="Owner", email="owner@example.test", password_hash="hash"),
            User(name="Other", email="other@example.test", password_hash="hash"),
        ]
    )
    test_session.commit()

    app = FastAPI()

    def override_db():
        yield test_session

    def override_user():
        return SimpleNamespace(id=1)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    service = FakeAIService()
    monkeypatch.setattr(ai_router, "ai_service", service)
    app.include_router(ai_router.router)
    app.include_router(conversation_router)
    yield TestClient(app), test_session

    test_session.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_chat_persists_messages_and_uses_database_history(client):
    http, db = client
    conversation = AIConversation(user_id=1, title="Authentication")
    db.add(conversation)
    db.commit()
    conversation_id = conversation.id

    first = http.post(
        "/ai/chat",
        json={"message": "First question", "conversation_id": conversation_id},
    )
    second = http.post(
        "/ai/chat",
        json={
            "message": "Follow up",
            "conversation_id": conversation_id,
            "history": [{"role": "assistant", "content": "Client supplied history"}],
        },
    )

    assert first.status_code == 200
    assert first.json()["conversation_id"] == conversation_id
    assert second.status_code == 200
    messages = http.get(f"/ai/conversations/{conversation_id}").json()["messages"]
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert messages[2]["content"] == "Follow up"
    assert messages[3]["metadata"]["model"]
    assert ai_router.ai_service.histories[1] == [
        {"role": "assistant", "content": "Response to: First question"},
        {"role": "user", "content": "First question"},
    ] or ai_router.ai_service.histories[1] == [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "Response to: First question"},
    ]
    assert all(
        item["content"] != "Client supplied history"
        for item in ai_router.ai_service.histories[1]
    )


def test_conversation_management_and_user_isolation(client):
    http, _ = client
    created = http.post(
        "/ai/conversations",
        json={"title": "First conversation"},
    )
    conversation_id = created.json()["id"]

    assert created.status_code == 201
    assert http.get("/ai/conversations").status_code == 200
    assert http.patch(
        f"/ai/conversations/{conversation_id}",
        json={"title": "Renamed"},
    ).json()["title"] == "Renamed"

    app = http.app
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=2)
    assert http.get(f"/ai/conversations/{conversation_id}").status_code == 404
    assert http.delete(f"/ai/conversations/{conversation_id}").status_code == 404
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)

    assert http.delete(f"/ai/conversations/{conversation_id}").status_code == 204
    assert http.get(f"/ai/conversations/{conversation_id}").status_code == 404


def test_conversation_can_be_cleared_and_reopened(client):
    http, _ = client
    created = http.post("/ai/conversations", json={"title": "Clearable"})
    conversation_id = created.json()["id"]
    result = http.post(
        "/ai/chat",
        json={"message": "Question", "conversation_id": conversation_id},
    )
    assert result.status_code == 200
    assert len(http.get(f"/ai/conversations/{conversation_id}").json()["messages"]) == 2

    cleared = http.delete(f"/ai/conversations/{conversation_id}/messages")
    assert cleared.status_code == 204
    assert http.get(f"/ai/conversations/{conversation_id}").json()["messages"] == []


def test_streaming_persists_messages_before_completion_event(client):
    http, _ = client
    conversation = http.post("/ai/conversations", json={"title": "Streaming"})
    conversation_id = conversation.json()["id"]

    response = http.post(
        "/ai/chat/stream",
        json={"message": "Persist this", "conversation_id": conversation_id},
    )

    events = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    completed = json.loads(events[-1])
    messages = http.get(f"/ai/conversations/{conversation_id}").json()["messages"]
    assert response.status_code == 200
    assert completed["type"] == "done"
    assert completed["response"]["conversation_id"] == conversation_id
    assert messages[-1]["content"] == "Streamed response to: Persist this"


def test_stream_failure_keeps_user_message_without_provider_details(
    client,
    monkeypatch,
):
    http, _ = client
    conversation = http.post("/ai/conversations", json={"title": "Interrupted"})
    conversation_id = conversation.json()["id"]
    monkeypatch.setattr(ai_router, "ai_service", FailingStreamingAIService())

    response = http.post(
        "/ai/chat/stream",
        json={"message": "This request should survive refresh", "conversation_id": conversation_id},
    )

    assert "provider credentials" not in response.text
    messages = http.get(f"/ai/conversations/{conversation_id}").json()["messages"]
    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "This request should survive refresh"),
    ]


def test_chat_rejects_a_conversation_from_another_user_project(client):
    http, db = client
    conversation = AIConversation(user_id=2, title="Private")
    db.add(conversation)
    db.commit()

    response = http.post(
        "/ai/chat",
        json={"message": "Do not reveal this", "conversation_id": conversation.id},
    )

    assert response.status_code == 404
