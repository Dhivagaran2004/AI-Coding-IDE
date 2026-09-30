from fastapi import FastAPI
from fastapi.testclient import TestClient

from App.api import repository_index_router
from App.auth.auth import get_current_user
from App.database.database import get_db
from App.models.project import Project


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
        if model is Project:
            return FakeQuery(self.project)
        raise AssertionError(f"Unexpected model: {model}")


class FakeIndexService:
    calls = []

    def __init__(self, db, project_id):
        self.project_id = project_id

    def index_project(self):
        self.calls.append(self.project_id)
        return {
            "project_id": self.project_id,
            "indexed_count": 1,
        }


def create_client(project):
    app = FastAPI()

    async def override_get_db():
        yield FakeDB(project)

    async def override_get_current_user():
        return FakeUser()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = (
        override_get_current_user
    )
    app.include_router(repository_index_router.router)
    return TestClient(app)


def test_index_route_runs_for_owned_project(monkeypatch):
    FakeIndexService.calls = []
    monkeypatch.setattr(
        repository_index_router,
        "RepositoryIndexService",
        FakeIndexService,
    )

    response = create_client(FakeProject()).post(
        "/projects/1/index"
    )

    assert response.status_code == 200
    assert response.json() == {
        "project_id": 1,
        "indexed_count": 1,
    }
    assert FakeIndexService.calls == [1]


def test_index_route_hides_projects_not_owned_by_user(monkeypatch):
    FakeIndexService.calls = []
    monkeypatch.setattr(
        repository_index_router,
        "RepositoryIndexService",
        FakeIndexService,
    )

    response = create_client(None).post(
        "/projects/1/index"
    )

    assert response.status_code == 404
    assert FakeIndexService.calls == []