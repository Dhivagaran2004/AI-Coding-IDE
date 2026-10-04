from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from App.api import ai_router
from App.auth.auth import get_current_user
from App.database.database import Base
from App.database.database import get_db
from App.models.ai_usage import AIUsage
from App.models.user import User
from App.service.AI.usage.usage_service import (
    AIUsageService,
    UsageLimitExceeded,
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        User(
            id=801,
            name="Usage One",
            email="usage-one@example.test",
            password_hash="hash",
            created_at=datetime.now(),
        ),
        User(
            id=802,
            name="Usage Two",
            email="usage-two@example.test",
            password_hash="hash",
            created_at=datetime.now(),
        ),
    ])
    session.commit()
    yield session
    session.close()
    engine.dispose()


def test_usage_is_recorded_and_configured_limits_are_per_user(db):
    service = AIUsageService(
        db,
        request_limit=1,
        token_limit=100,
        output_reservation=0,
    )
    usage_id = service.start_request(
        user_id=801,
        project_id=None,
        conversation_id=None,
        provider="mock",
        model="test-model",
        estimated_input_tokens=10,
    )
    service.finish_request(
        usage_id,
        input_tokens=10,
        output_tokens=7,
        request_status="succeeded",
    )

    record = db.query(AIUsage).filter(AIUsage.id == usage_id).one()
    assert record.user_id == 801
    assert record.provider == "mock"
    assert record.model == "test-model"
    assert record.total_tokens == 17
    assert record.token_count_source == "estimated"
    assert record.request_status == "succeeded"
    assert record.error_status is None

    with pytest.raises(UsageLimitExceeded, match="request"):
        service.start_request(801, None, None, "mock", "test", 1)
    other_user_id = service.start_request(802, None, None, "mock", "test", 1)
    assert other_user_id != usage_id


def test_token_limit_includes_in_flight_reservations_and_configuration_updates(db):
    restrictive = AIUsageService(
        db,
        request_limit=0,
        token_limit=20,
        output_reservation=5,
    )
    first_id = restrictive.start_request(
        801, None, None, "mock", "test", 10
    )
    with pytest.raises(UsageLimitExceeded, match="token"):
        restrictive.start_request(801, None, None, "mock", "test", 6)

    permissive = AIUsageService(
        db,
        request_limit=0,
        token_limit=100,
        output_reservation=0,
    )
    second_id = permissive.start_request(
        801, None, None, "mock", "test", 6
    )
    permissive.finish_request(
        second_id,
        input_tokens=6,
        output_tokens=3,
        request_status="failed",
        error_status="provider_server_error",
    )

    assert first_id != second_id
    assert db.query(AIUsage).filter(AIUsage.id == second_id).one().error_status == (
        "provider_server_error"
    )


def test_chat_and_stream_each_create_one_usage_record(db, monkeypatch):
    class FakeAIService:
        async def chat(self, message, context=None, history=None):
            return "A normal response."

        async def stream(self, message, context=None, history=None):
            yield "A "
            yield "streamed response."

    app = FastAPI()

    def override_db():
        yield db

    def override_user():
        return SimpleNamespace(id=801)

    monkeypatch.setattr(ai_router, "ai_service", FakeAIService())
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    app.include_router(ai_router.router)
    client = TestClient(app)

    ordinary = client.post("/ai/chat", json={"message": "Question"})
    streamed = client.post(
        "/ai/chat/stream",
        json={"message": "Question", "project_id": None},
    )
    records = db.query(AIUsage).order_by(AIUsage.id).all()

    assert ordinary.status_code == 200
    assert streamed.status_code == 200
    assert len(records) == 2
    assert [record.request_status for record in records] == [
        "succeeded",
        "succeeded",
    ]
    assert all(record.total_tokens > 0 for record in records)
    assert "A streamed response." in streamed.text


def test_api_rejects_usage_over_limit_without_calling_provider(db, monkeypatch):
    class FakeAIService:
        calls = 0

        async def chat(self, message, context=None, history=None):
            self.calls += 1
            return "response"

    fake_service = FakeAIService()
    app = FastAPI()

    def override_db():
        yield db

    def override_user():
        return SimpleNamespace(id=801)

    monkeypatch.setattr(ai_router, "ai_service", fake_service)
    monkeypatch.setattr(ai_router, "AI_DAILY_REQUEST_LIMIT", 1)
    monkeypatch.setattr(ai_router, "AI_DAILY_TOKEN_LIMIT", 10000)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    app.include_router(ai_router.router)
    client = TestClient(app)

    first = client.post("/ai/chat", json={"message": "First"})
    second = client.post("/ai/chat", json={"message": "Second"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert "request limit" in second.json()["detail"]
    assert fake_service.calls == 1


def test_failed_stream_is_recorded_once_with_partial_output(db, monkeypatch):
    class InterruptedAIService:
        async def chat(self, message, context=None, history=None):
            raise AssertionError("stream endpoint must use stream()")

        async def stream(self, message, context=None, history=None):
            yield "partial response"
            raise TimeoutError("private provider detail")

    app = FastAPI()

    def override_db():
        yield db

    def override_user():
        return SimpleNamespace(id=801)

    monkeypatch.setattr(ai_router, "ai_service", InterruptedAIService())
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    app.include_router(ai_router.router)
    response = TestClient(app).post(
        "/ai/chat/stream",
        json={"message": "Explain the failure"},
    )
    records = db.query(AIUsage).all()

    assert response.status_code == 200
    assert "partial response" in response.text
    assert "private provider detail" not in response.text
    assert len(records) == 1
    assert records[0].request_status == "failed"
    assert records[0].output_tokens > 0
    assert records[0].reserved_tokens == 0