from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from App.api import terminal_router
from App.auth.auth import get_current_user
from App.database.database import get_db
from App.models.project import Project
from App.models.project_file import ProjectFile
from App.schema.terminal_schema import TerminalCommandRequest
from App.service.Terminal.code_execution_service import (
    CodeExecutionResult,
    ExecutionQueueFull,
    SandboxUnavailable,
    UnsupportedSourceFile,
)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *criteria):
        rows = self.rows
        for criterion in criteria:
            column = getattr(criterion, "left", None)
            value = getattr(getattr(criterion, "right", None), "value", None)
            key = getattr(column, "key", None)
            if key is not None:
                rows = [row for row in rows if getattr(row, key) == value]
        return FakeQuery(rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self, project=None, files=None):
        self.project = project
        self.files = files or []

    def query(self, model):
        if model is Project:
            return FakeQuery([self.project] if self.project else [])
        if model is ProjectFile:
            return FakeQuery(self.files)
        raise AssertionError(f"Unexpected model: {model}")


def create_client(monkeypatch, project=None, files=None, user_id=5):
    app = FastAPI()
    db = FakeDB(project=project, files=files)
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    app.include_router(terminal_router.router)
    return TestClient(app), db


def owned_project():
    return SimpleNamespace(id=1, user_id=5)


def source_file():
    return SimpleNamespace(
        id=10,
        project_id=1,
        parent_id=None,
        name="main.py",
        type="file",
        content="print('saved')",
    )


def test_run_current_file_returns_sandbox_result(monkeypatch):
    client, _db = create_client(
        monkeypatch,
        project=owned_project(),
        files=[source_file()],
    )
    received = {}

    def execute_file(**kwargs):
        received.update(kwargs)
        return CodeExecutionResult(0, "API Test\n", "", True)

    monkeypatch.setattr(
        terminal_router.code_execution_service,
        "execute_file",
        execute_file,
    )

    response = client.post(
        "/projects/1/terminal/run",
        json={"file_id": 10, "code": "print('API Test')"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "exit_code": 0,
        "stdout": "API Test\n",
        "stderr": "",
        "success": True,
    }
    assert received["project_id"] == 1
    assert received["file_id"] == 10
    assert received["project_files"] == [source_file()]
    assert received["code"] == "print('API Test')"


def test_run_requires_authentication(monkeypatch):
    app = FastAPI()
    app.include_router(terminal_router.router)
    response = TestClient(app).post(
        "/projects/1/terminal/run",
        json={"file_id": 10, "code": "print('no')"},
    )

    assert response.status_code == 401


def test_non_owner_cannot_run_project_file(monkeypatch):
    client, _db = create_client(
        monkeypatch,
        project=SimpleNamespace(id=1, user_id=99),
        files=[source_file()],
    )
    called = False

    def execute_file(**_kwargs):
        nonlocal called
        called = True
        return CodeExecutionResult(0, "", "", True)

    monkeypatch.setattr(
        terminal_router.code_execution_service,
        "execute_file",
        execute_file,
    )
    response = client.post(
        "/projects/1/terminal/run",
        json={"file_id": 10, "code": "print('no')"},
    )

    assert response.status_code == 404
    assert called is False


def test_missing_project_cannot_run_code(monkeypatch):
    client, _db = create_client(monkeypatch, project=None)
    response = client.post(
        "/projects/1/terminal/run",
        json={"file_id": 10, "code": "print('no')"},
    )

    assert response.status_code == 404


def test_run_rejects_invalid_payload(monkeypatch):
    client, _db = create_client(monkeypatch, project=owned_project())
    response = client.post(
        "/projects/1/terminal/run",
        json={"file_id": 0, "code": ""},
    )

    assert response.status_code == 422


def test_run_reports_unsupported_file(monkeypatch):
    client, _db = create_client(
        monkeypatch,
        project=owned_project(),
        files=[source_file()],
    )
    monkeypatch.setattr(
        terminal_router.code_execution_service,
        "execute_file",
        lambda **_kwargs: (_ for _ in ()).throw(
            UnsupportedSourceFile("The selected file language is not supported.")
        ),
    )

    response = client.post(
        "/projects/1/terminal/run",
        json={"file_id": 10, "code": "contents"},
    )

    assert response.status_code == 422


def test_run_reports_queue_full(monkeypatch):
    client, _db = create_client(
        monkeypatch,
        project=owned_project(),
        files=[source_file()],
    )
    monkeypatch.setattr(
        terminal_router.code_execution_service,
        "execute_file",
        lambda **_kwargs: (_ for _ in ()).throw(
            ExecutionQueueFull("The code execution queue is full.")
        ),
    )

    response = client.post(
        "/projects/1/terminal/run",
        json={"file_id": 10, "code": "contents"},
    )

    assert response.status_code == 429


def test_run_reports_unavailable_sandbox(monkeypatch):
    client, _db = create_client(
        monkeypatch,
        project=owned_project(),
        files=[source_file()],
    )
    monkeypatch.setattr(
        terminal_router.code_execution_service,
        "execute_file",
        lambda **_kwargs: (_ for _ in ()).throw(
            SandboxUnavailable("Docker is unavailable on the server.")
        ),
    )

    response = client.post(
        "/projects/1/terminal/run",
        json={"file_id": 10, "code": "contents"},
    )

    assert response.status_code == 503


def test_host_shell_execution_is_retired(monkeypatch):
    client, _db = create_client(monkeypatch, project=owned_project())
    response = client.post(
        "/projects/1/terminal/execute",
        json={"command": "python main.py"},
    )

    assert response.status_code == 410


def test_empty_legacy_command_is_rejected(monkeypatch):
    client, _db = create_client(monkeypatch, project=owned_project())
    response = client.post(
        "/projects/1/terminal/execute",
        json={"command": ""},
    )

    assert response.status_code == 400