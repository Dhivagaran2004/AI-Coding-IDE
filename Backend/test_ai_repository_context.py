import hashlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from App.api import ai_router
from App.auth.auth import get_current_user
from App.database.database import get_db
from App.models.project import Project
from App.models.repository_index import RepositoryIndex
from App.models.repository_chunk import RepositoryChunk
from App.service.AI.index.repository_index_service import (
    RepositoryIndexService,
)


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

    def first(self):
        return self.files[0] if self.files else None


class FakeDB:
    def __init__(self, files, project_accessible=True, indexes=None):
        self.files = files
        self.project_accessible = project_accessible
        self.indexes = indexes or []

    def query(self, model):
        if model is Project:
            projects = (
                [FakeProject(id=1, user_id=1)]
                if self.project_accessible
                else []
            )
            return FakeQuery(projects)
        if model is RepositoryIndex:
            return FakeQuery(self.indexes)
        if model is RepositoryChunk:
            return FakeQuery([])
        return FakeQuery(self.files)


class FakeProject:
    def __init__(self, id, user_id):
        self.id = id
        self.user_id = user_id


class FakeIndex:
    def __init__(self, file_id, content, status="ready"):
        self.project_id = 1
        self.file_id = file_id
        self.content_hash = hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()
        self.indexed_content = content
        self.status = status


class FakeUser:
    id = 1


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


def create_test_app(
    fake_service,
    fake_files,
    project_accessible=True,
    indexes=None,
):
    app = FastAPI()

    async def override_get_db():
        yield FakeDB(
            fake_files,
            project_accessible=project_accessible,
            indexes=indexes,
        )

    async def override_get_current_user():
        return FakeUser()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = (
        override_get_current_user
    )

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


def test_project_tree_is_added_for_file_inventory_questions():
    fake_service = FakeAIService()
    fake_files = [
        FakeFile(
            file_id=1,
            name="src",
            content="",
            file_type="folder",
        ),
        FakeFile(
            file_id=2,
            name="main.py",
            content="print('hello')",
            parent_id=1,
        ),
    ]
    app, original_service = create_test_app(
        fake_service,
        fake_files,
    )

    try:
        response = TestClient(app).post(
            "/ai/chat",
            json={
                "message": "List the files available in this project.",
                "project_id": 1,
            },
        )

        assert response.status_code == 200
        assert fake_service.last_context is not None
        assert "PROJECT STRUCTURE" in fake_service.last_context
        assert "src/" in fake_service.last_context
        assert "main.py" in fake_service.last_context
    finally:
        ai_router.ai_service = original_service


def test_project_id_rejects_project_not_owned_by_user():
    fake_service = FakeAIService()
    app, original_service = create_test_app(
        fake_service,
        [],
        project_accessible=False,
    )

    try:
        response = TestClient(app).post(
            "/ai/chat",
            json={
                "message": "Read another project.",
                "project_id": 1,
            },
        )

        assert response.status_code == 404
        assert fake_service.last_message is None
    finally:
        ai_router.ai_service = original_service


def test_selected_code_metadata_is_prioritized_in_ai_context():
    fake_service = FakeAIService()
    app, original_service = create_test_app(
        fake_service,
        [],
    )

    try:
        response = TestClient(app).post(
            "/ai/chat",
            json={
                "message": "Explain this selected code.",
                "context": "full current file contents",
                "selected_code": {
                    "file_path": "Backend/App/main.py",
                    "language": "python",
                    "code": "return value / count",
                    "start_line": 10,
                    "end_line": 12,
                },
                "project_id": 1,
            },
        )

        assert response.status_code == 200
        assert "FILE: Backend/App/main.py" in fake_service.last_context
        assert "LANGUAGE: python" in fake_service.last_context
        assert "LINES: 10-12" in fake_service.last_context
        assert "return value / count" in fake_service.last_context
        assert fake_service.last_context.index("SELECTED CODE CONTEXT") < (
            fake_service.last_context.index("CURRENT FILE CONTEXT")
        )
    finally:
        ai_router.ai_service = original_service


def test_selected_code_rejects_oversized_payload():
    fake_service = FakeAIService()
    app, original_service = create_test_app(fake_service, [])

    try:
        response = TestClient(app).post(
            "/ai/chat",
            json={
                "message": "Explain this selected code.",
                "selected_code": {
                    "file_path": "main.py",
                    "language": "python",
                    "code": "x" * 20001,
                    "start_line": 1,
                    "end_line": 1,
                },
                "project_id": 1,
            },
        )

        assert response.status_code == 422
        assert fake_service.last_message is None
    finally:
        ai_router.ai_service = original_service


def test_editor_context_requires_project_id():
    fake_service = FakeAIService()
    app, original_service = create_test_app(fake_service, [])

    try:
        client = TestClient(app)
        selected_response = client.post(
            "/ai/chat",
            json={
                "message": "Explain this selection.",
                "selected_code": {
                    "file_path": "main.py",
                    "language": "python",
                    "code": "return True",
                    "start_line": 1,
                    "end_line": 1,
                },
            },
        )
        terminal_response = client.post(
            "/ai/chat",
            json={
                "message": "Fix this error.",
                "terminal_context": {
                    "command": "pytest",
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "failed",
                    "success": False,
                },
            },
        )

        assert selected_response.status_code == 422
        assert terminal_response.status_code == 422
        assert fake_service.last_message is None
    finally:
        ai_router.ai_service = original_service


def test_terminal_context_preserves_failure_details_and_redacts_secrets():
    fake_service = FakeAIService()
    app, original_service = create_test_app(fake_service, [])

    try:
        response = TestClient(app).post(
            "/ai/chat",
            json={
                "message": "Fix this error.",
                "terminal_context": {
                    "command": "python -m pytest",
                    "exit_code": 1,
                    "stdout": "1 failed",
                    "stderr": (
                        "ModuleNotFoundError: missing module\n"
                        "API_KEY=do-not-send\n"
                        "password: do-not-send-either\n"
                        "hf_123456789012345678901234\n"
                        "postgresql://user:db-password@host/db"
                    ),
                    "success": False,
                },
                "project_id": 1,
            },
        )

        assert response.status_code == 200
        assert "STDERR:" in fake_service.last_context
        assert "ModuleNotFoundError: missing module" in fake_service.last_context
        assert "EXIT CODE: 1" in fake_service.last_context
        assert "COMMAND: python -m pytest" in fake_service.last_context
        assert "STDOUT:\n1 failed" in fake_service.last_context
        assert "[environment variable omitted]" in fake_service.last_context
        assert "[REDACTED]" in fake_service.last_context
        assert "do-not-send" not in fake_service.last_context
        assert "hf_123456789012345678901234" not in fake_service.last_context
        assert "db-password" not in fake_service.last_context
        assert fake_service.last_context.index("STDERR:") < (
            fake_service.last_context.index("EXIT CODE:")
        )
    finally:
        ai_router.ai_service = original_service


def test_terminal_diagnostics_precede_selection_and_current_file():
    fake_service = FakeAIService()
    app, original_service = create_test_app(fake_service, [])

    try:
        response = TestClient(app).post(
            "/ai/chat",
            json={
                "message": "Fix this terminal error in the selected function.",
                "context": "current file source",
                "selected_code": {
                    "file_path": "src/main.py",
                    "language": "python",
                    "code": "return broken_call()",
                    "start_line": 3,
                    "end_line": 3,
                },
                "terminal_context": {
                    "command": "pytest",
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "NameError: broken_call",
                    "success": False,
                },
                "project_id": 1,
            },
        )

        assert response.status_code == 200
        assert fake_service.last_context.index("STDERR:") < (
            fake_service.last_context.index("SELECTED CODE CONTEXT")
        )
        assert fake_service.last_context.index("SELECTED CODE CONTEXT") < (
            fake_service.last_context.index("CURRENT FILE CONTEXT")
        )
    finally:
        ai_router.ai_service = original_service


def test_retrieved_chunks_precede_full_file_in_prioritized_context():
    fake_service = FakeAIService()
    app, original_service = create_test_app(
        fake_service,
        [
            FakeFile(
                1,
                "auth_handler.py",
                "def broken_call():\n    raise NameError()\n",
            )
        ],
    )

    try:
        response = TestClient(app).post(
            "/ai/chat",
            json={
                "message": "Explain auth_handler.py and broken_call.",
                "context": "entire editor buffer",
                "selected_code": {
                    "file_path": "src/main.py",
                    "language": "python",
                    "code": "return broken_call()",
                    "start_line": 4,
                    "end_line": 4,
                },
                "project_id": 1,
            },
        )

        assert response.status_code == 200
        context = fake_service.last_context
        assert "REPOSITORY CONTEXT" in context
        assert context.index("SELECTED CODE CONTEXT") < (
            context.index("REPOSITORY CONTEXT")
        )
        assert context.index("REPOSITORY CONTEXT") < (
            context.index("CURRENT FILE CONTEXT")
        )
    finally:
        ai_router.ai_service = original_service


def test_terminal_context_truncates_output_and_allows_empty_output():
    fake_service = FakeAIService()
    app, original_service = create_test_app(fake_service, [])

    try:
        response = TestClient(app).post(
            "/ai/chat",
            json={
                "message": "What happened?",
                "terminal_context": {
                    "command": "python check.py",
                    "exit_code": 0,
                    "stdout": "x" * 13000,
                    "stderr": "",
                    "success": True,
                },
                "project_id": 1,
            },
        )

        assert response.status_code == 200
        assert len(fake_service.last_context.split("STDOUT:\n", 1)[1]) == 12000
        assert "STDERR:\n\nEXIT CODE: 0" in fake_service.last_context
    finally:
        ai_router.ai_service = original_service


def test_terminal_context_still_requires_project_ownership():
    fake_service = FakeAIService()
    app, original_service = create_test_app(
        fake_service,
        [],
        project_accessible=False,
    )

    try:
        response = TestClient(app).post(
            "/ai/chat",
            json={
                "message": "Fix this error.",
                "terminal_context": {
                    "command": "pytest",
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "failed",
                    "success": False,
                },
                "project_id": 1,
            },
        )

        assert response.status_code == 404
        assert fake_service.last_message is None
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


def test_project_id_uses_matching_ready_indexed_context():
    fake_service = FakeAIService()
    file_content = "def calculate_total(items):\n    return sum(items)\n"
    fake_files = [
        FakeFile(
            file_id=1,
            name="main.py",
            content=file_content,
        ),
    ]
    app, original_service = create_test_app(
        fake_service,
        fake_files,
        indexes=[FakeIndex(1, file_content)],
    )

    try:
        response = TestClient(app).post(
            "/ai/chat",
            json={
                "message": "Explain calculate_total",
                "project_id": 1,
            },
        )

        assert response.status_code == 200
        assert "Indexed Repository Context" in fake_service.last_context
        assert "calculate_total" in fake_service.last_context
    finally:
        ai_router.ai_service = original_service