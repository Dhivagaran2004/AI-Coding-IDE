from fastapi import FastAPI
from fastapi.testclient import TestClient

from App.api import ai_router
from App.auth.auth import get_current_user
from App.database.database import get_db


class FakeAIService:
    def __init__(self, response):
        self.response = response

    async def chat(self, message, context=None, history=None):
        return self.response


def make_client(response):
    app = FastAPI()

    async def override_db():
        yield object()

    async def override_user():
        return type("UserIdentity", (), {"id": 1})()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    original_service = ai_router.ai_service
    ai_router.ai_service = FakeAIService(response)
    app.include_router(ai_router.router)
    return TestClient(app), original_service


def test_chat_extracts_valid_structured_code_action():
    response_text = """I will change the function safely.
```json
{
  "type": "code_change",
  "operation": "replace",
  "file_path": "Backend/main.py",
  "description": "Add error handling",
  "start_line": 1,
  "end_line": 1,
  "old_code": "return value\\n",
  "new_code": "try:\\n    return value\\nexcept ValueError:\\n    return None\\n"
}
```"""
    client, original_service = make_client(response_text)

    try:
        result = client.post(
            "/ai/chat",
            json={"message": "Add error handling"},
        )

        assert result.status_code == 200
        action = result.json()["code_action"]
        assert action["type"] == "code_change"
        assert action["operation"] == "replace"
        assert action["file_path"] == "Backend/main.py"
        assert action["old_code"] == "return value\n"
    finally:
        ai_router.ai_service = original_service


def test_chat_omits_invalid_structured_code_action():
    client, original_service = make_client(
        "```json\n{\"type\": \"code_change\", \"operation\": \"replace\"}\n```"
    )

    try:
        result = client.post(
            "/ai/chat",
            json={"message": "Suggest a change"},
        )

        assert result.status_code == 200
        assert result.json()["code_action"] is None
    finally:
        ai_router.ai_service = original_service
