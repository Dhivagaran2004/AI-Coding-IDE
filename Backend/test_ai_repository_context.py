from fastapi import FastAPI
from fastapi.testclient import TestClient

from App.api import ai_router
from App.database.database import get_db


class FakeAIService:
    def __init__(self):
        self.last_message = None
        self.last_context = None
        self.last_history = None

    async def chat(
        self,
        message: str,
        context: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        self.last_message = message
        self.last_context = context
        self.last_history = history

        return "Fake AI response"


class FakeQuery:
    def __init__(self, files):
        self.files = files

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self.files


class FakeDB:
    def __init__(self, files):
        self.files = files

    def query(self, model):
        return FakeQuery(self.files)


class FakeFile:
    def __init__(
        self,
        file_id,
        name,
        content,
        file_type="file",
        parent_id=None,
    ):
        self.id = file_id
        self.project_id = 1
        self.name = name
        self.content = content
        self.type = file_type
        self.parent_id = parent_id
        self.language = "python"


def create_test_app(fake_service, fake_files):
    app = FastAPI()

    async def override_get_db():
        yield FakeDB(fake_files)

    app.dependency_overrides[get_db] = override_get_db

    original_service = ai_router.ai_service
    ai_router.ai_service = fake_service

    app.include_router(ai_router.router)

    return app, original_service


def test_project_id_adds_repository_context(monkeypatch):
    fake_service = FakeAIService()

    fake_files = [
        FakeFile(
            file_id=1,
            name="main.py",
            content=(
                "def calculate_total(items):\n"
                "    return sum(items)\n"
            ),
        ),
        FakeFile(
            file_id=2,
            name="utils.py",
            content=(
                "def helper():\n"
                "    return True\n"
            ),
        ),
    ]

    app, original_service = create_test_app(
        fake_service,
        fake_files,
    )

    try:
        client = TestClient(app)

        response = client.post(
            "/ai/chat",
            json={
                "message": "Explain the calculate_total function.",
                "project_id": 1,
            },
        )

        assert response.status_code == 200

        data = response.json()

        assert data["message"] == "Fake AI response"

        assert fake_service.last_message == (
            "Explain the calculate_total function."
        )

        assert fake_service.last_context is not None

        assert "main.py" in fake_service.last_context

        assert "calculate_total" in fake_service.last_context

        assert "return sum(items)" in fake_service.last_context

    finally:
        ai_router.ai_service = original_service


def test_project_id_without_repository_match_still_calls_ai(
    monkeypatch,
):
    fake_service = FakeAIService()

    fake_files = [
        FakeFile(
            file_id=1,
            name="main.py",
            content="def hello():\n    return 'hello'\n",
        ),
    ]

    app, original_service = create_test_app(
        fake_service,
        fake_files,
    )

    try:
        client = TestClient(app)

        response = client.post(
            "/ai/chat",
            json={
                "message": "Tell me about authentication.",
                "project_id": 1,
            },
        )

        assert response.status_code == 200

        assert fake_service.last_message == (
            "Tell me about authentication."
        )

    finally:
        ai_router.ai_service = original_service