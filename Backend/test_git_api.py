import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from App.api import git_router
from App.auth.auth import get_current_user
from App.database.database import Base, get_db
from App.models.project import Project
from App.models.user import User


def test_git_routes_require_project_ownership_and_return_read_only_state(
    tmp_path,
    monkeypatch,
):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    test_session = sessionmaker(bind=engine)()
    test_session.add_all(
        [
            User(id=1, name="Owner", email="git-owner@example.test", password_hash="x"),
            User(id=2, name="Other", email="git-other@example.test", password_hash="x"),
        ]
    )
    test_session.flush()
    test_session.add(Project(id=1, user_id=1, name="Owned", language="python"))
    test_session.commit()

    subprocess.run(["git", "-C", str(tmp_path), "init", "-b", "main"], check=True)
    (tmp_path / "example.py").write_text("value = 1\n", encoding="utf-8")

    class WorkspaceService:
        def validate_project_directory(self, project_id):
            assert project_id == 1
            return tmp_path

    monkeypatch.setattr(git_router, "terminal_service", WorkspaceService())
    app = FastAPI()

    def override_db():
        yield test_session

    def override_user():
        return type("Identity", (), {"id": 1})()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    app.include_router(git_router.router)
    client = TestClient(app)

    status = client.get("/projects/1/git/status")
    diff = client.get("/projects/1/git/diff")
    assert status.status_code == 200
    assert status.json()["branch"] == "main"
    assert status.json()["untracked_files"] == ["example.py"]
    assert diff.status_code == 200
    assert diff.json()["diff"] == ""

    app.dependency_overrides[get_current_user] = lambda: type(
        "Identity", (), {"id": 2}
    )()
    assert client.get("/projects/1/git/status").status_code == 404

    test_session.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
