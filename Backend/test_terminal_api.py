from fastapi import FastAPI
from fastapi.testclient import TestClient

from App.api import terminal_router
from App.auth.auth import get_current_user
from App.database.database import get_db
from App.models.project import Project
from App.service.Terminal.terminal_service import TerminalService


class FakeProject:
    id = 1
    user_id = 1


class FakeUser:
    id = 1


class FakeQuery:
    def __init__(self, result):
        self.result = result

    def filter(self, *args):
        return self

    def first(self):
        return self.result


class FakeDB:
    def __init__(self, project):
        self.project = project

    def query(self, model):
        assert model is Project
        return FakeQuery(self.project)


def make_client(tmp_path, monkeypatch, project=FakeProject()):
    app = FastAPI()

    async def override_db():
        yield FakeDB(project)

    async def override_user():
        return FakeUser()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    monkeypatch.setattr(
        terminal_router,
        "terminal_service",
        TerminalService(str(tmp_path)),
    )
    app.include_router(terminal_router.router)
    return TestClient(app)


def test_execute_terminal_command(tmp_path, monkeypatch):
    project_directory = tmp_path / "1"
    project_directory.mkdir()
    (project_directory / "sample.txt").write_text(
        "API Test",
        encoding="utf-8",
    )
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/projects/1/terminal/execute",
        json={"command": "cat sample.txt"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["exit_code"] == 0
    assert data["success"] is True
    assert "API Test" in data["stdout"]


def test_terminal_rejects_unallowlisted_commands(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    marker = tmp_path / "outside-marker"

    response = client.post(
        "/projects/1/terminal/execute",
        json={
            "command": (
                "python -c \"from pathlib import Path; "
                f"Path('{marker}').touch()\""
            )
        },
    )

    assert response.status_code == 400
    assert not marker.exists()
    assert "Shell operators" in response.json()["detail"]


def test_terminal_rejects_paths_outside_project(tmp_path, monkeypatch):
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("must not be read", encoding="utf-8")
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/projects/1/terminal/execute",
        json={"command": "cat ../outside.txt"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "must not be read" not in response.text
    assert "relative paths inside the project" in response.json()["stderr"]


def test_empty_terminal_command(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    response = client.post(
        "/projects/1/terminal/execute",
        json={
            "command": ""
        },
    )

    assert response.status_code == 400


def test_terminal_access_requires_project_ownership(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, project=None)

    response = client.post(
        "/projects/1/terminal/execute",
        json={"command": "python -c \"print('must not run')\""},
    )

    assert response.status_code == 404
    assert "must not run" not in response.text