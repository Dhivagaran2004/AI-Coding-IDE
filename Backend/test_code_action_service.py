from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from App.database.database import Base
from App.database.database import get_db
from App.auth.auth import get_current_user
from App.models.project import Project
from App.models.project_file import ProjectFile
from App.models.user import User
from App.schema.ai_schema import CodeAction
from App.service.AI.code_action_service import (
    CodeActionService,
    PatchNotFoundError,
    PatchValidationError,
    StalePatchError,
    UnsupportedPatchOperation,
)
from App.routes.project_file import router as project_file_router


class RecordingVectorIndexer:
    def __init__(self):
        self.indexed_file_ids = []

    def index_file(self, file):
        self.indexed_file_ids.append(file.id)


@pytest.fixture
def patch_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        User(
            id=1,
            name="Owner One",
            email="owner1@example.test",
            password_hash="x",
            created_at=datetime.now(),
        ),
        User(
            id=2,
            name="Owner Two",
            email="owner2@example.test",
            password_hash="x",
            created_at=datetime.now(),
        ),
    ])
    session.flush()
    session.add_all([
        Project(id=1, user_id=1, name="One", language="python"),
        Project(id=2, user_id=2, name="Two", language="python"),
    ])
    session.flush()
    session.add_all([
        ProjectFile(
            id=11,
            project_id=1,
            name="main.py",
            type="file",
            content="def calculate():\n    return 1\n",
            language="python",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
        ProjectFile(
            id=22,
            project_id=2,
            name="private.py",
            type="file",
            content="secret = True\n",
            language="python",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
    ])
    session.commit()
    yield session
    session.close()
    engine.dispose()


def make_action(**overrides):
    values = {
        "type": "code_change",
        "operation": "replace",
        "file_path": "main.py",
        "description": "Return the corrected value",
        "start_line": 2,
        "end_line": 2,
        "old_code": "    return 1\n",
        "new_code": "    return 2\n",
    }
    values.update(overrides)
    return CodeAction(**values)


def make_service(db):
    vector_indexer = RecordingVectorIndexer()
    return CodeActionService(db, vector_index_service=vector_indexer), vector_indexer


def test_valid_patch_applies_after_explicit_service_call_and_reindexes(patch_db):
    service, vector_indexer = make_service(patch_db)

    updated = service.apply_action(1, 11, 1, make_action())

    assert updated.content == "def calculate():\n    return 2\n"
    assert vector_indexer.indexed_file_ids == [11]


def test_patch_remains_applied_when_repository_indexing_fails(
    patch_db,
    monkeypatch,
    caplog,
):
    def fail_indexing(self, file):
        raise SQLAlchemyError("repository index schema is unavailable")

    monkeypatch.setattr(
        "App.service.AI.code_action_service.RepositoryIndexService.create_or_update_index",
        fail_indexing,
    )
    service, _ = make_service(patch_db)

    updated = service.apply_action(1, 11, 1, make_action())

    assert updated.content == "def calculate():\n    return 2\n"
    assert patch_db.get(ProjectFile, 11).content == updated.content
    assert "Project file 11 was saved, but repository indexing failed." in caplog.text


def test_patch_matches_lf_action_against_crlf_file_and_preserves_eol(patch_db):
    file = patch_db.get(ProjectFile, 11)
    setattr(file, "content", "def calculate():\r\n    return 1\r\n")
    patch_db.commit()
    service, _ = make_service(patch_db)

    updated = service.apply_action(1, 11, 1, make_action())

    assert getattr(updated, "content") == (
        "def calculate():\r\n    return 2\r\n"
    )


def test_patch_rebases_indentation_when_model_omits_source_indent(patch_db):
    file = patch_db.get(ProjectFile, 11)
    setattr(file, "content", "def calculate():\n    return 1\n")
    patch_db.commit()
    service, _ = make_service(patch_db)
    action = make_action(
        start_line=2,
        end_line=2,
        old_code="return 1",
        new_code="return 2",
    )

    updated = service.apply_action(1, 11, 1, action)

    assert getattr(updated, "content") == "def calculate():\n    return 2\n"


def test_invalid_or_cross_project_file_is_rejected(patch_db):
    service, _ = make_service(patch_db)

    with pytest.raises(PatchNotFoundError):
        service.apply_action(1, 999, 1, make_action())
    with pytest.raises(PatchNotFoundError):
        service.apply_action(1, 22, 1, make_action())


def test_wrong_project_owner_is_rejected(patch_db):
    service, _ = make_service(patch_db)

    with pytest.raises(PatchNotFoundError):
        service.apply_action(2, 22, 1, make_action(file_path="private.py"))


@pytest.mark.parametrize(
    "file_path",
    ["../outside.py", "src/../../outside.py", "/etc/passwd", "C:/outside.py", "src\\main.py"],
)
def test_path_traversal_and_absolute_paths_are_rejected(patch_db, file_path):
    service, _ = make_service(patch_db)

    with pytest.raises(PatchValidationError):
        service.apply_action(1, 11, 1, make_action(file_path=file_path))


def test_wrong_file_path_is_rejected(patch_db):
    service, _ = make_service(patch_db)

    with pytest.raises(PatchValidationError):
        service.apply_action(1, 11, 1, make_action(file_path="other.py"))


def test_stale_patch_is_rejected_without_overwriting(patch_db):
    service, _ = make_service(patch_db)
    file = patch_db.get(ProjectFile, 11)
    file.content = "def calculate():\n    return 9\n"
    patch_db.commit()

    with pytest.raises(StalePatchError):
        service.apply_action(1, 11, 1, make_action())
    assert file.content.endswith("return 9\n")


def test_patch_finds_unique_old_code_when_line_number_has_shifted(patch_db):
    file = patch_db.get(ProjectFile, 11)
    file.content = "# inserted before proposal\n" + file.content
    patch_db.commit()
    service, _ = make_service(patch_db)

    updated = service.apply_action(
        1,
        11,
        1,
        make_action(start_line=1, end_line=1),
    )

    assert updated.content == "# inserted before proposal\ndef calculate():\n    return 2\n"


def test_patch_rejects_ambiguous_old_code_when_line_number_has_shifted(patch_db):
    file = patch_db.get(ProjectFile, 11)
    file.content = "def calculate():\n    return 1\n\ndef other():\n    return 1\n"
    patch_db.commit()
    service, _ = make_service(patch_db)

    with pytest.raises(StalePatchError):
        service.apply_action(
            1,
            11,
            1,
            make_action(start_line=1, end_line=1),
        )


def test_invalid_line_range_is_rejected(patch_db):
    service, _ = make_service(patch_db)

    with pytest.raises(PatchValidationError):
        service.apply_action(
            1,
            11,
            1,
            make_action(start_line=10, end_line=10),
        )


def test_oversized_patch_is_rejected_by_schema_and_result_limit(patch_db):
    with pytest.raises(ValidationError):
        make_action(new_code="x" * 20001)

    service, _ = make_service(patch_db)
    service.MAX_RESULTING_FILE_CHARS = 10
    with pytest.raises(PatchValidationError):
        service.apply_action(1, 11, 1, make_action())


@pytest.mark.parametrize("operation", ["insert", "delete", "create_file"])
def test_non_replace_operations_are_validated_but_not_applied(
    patch_db,
    operation,
):
    service, _ = make_service(patch_db)
    action = make_action(operation=operation)

    with pytest.raises(UnsupportedPatchOperation):
        service.apply_action(1, 11, 1, action)


def test_invalid_line_order_rejected_by_schema():
    with pytest.raises(ValidationError):
        make_action(start_line=5, end_line=4)


def test_patch_endpoint_applies_only_after_authenticated_approval(patch_db):
    app = FastAPI()

    async def override_db():
        yield patch_db

    async def override_user():
        return type("UserIdentity", (), {"id": 1})()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    app.include_router(project_file_router)

    response = TestClient(app).post(
        "/projects/1/files/11/patch",
        json=make_action().model_dump(),
    )

    assert response.status_code == 200
    assert response.json()["content"] == "def calculate():\n    return 2\n"


def test_patch_endpoint_rejects_wrong_owner(patch_db):
    app = FastAPI()

    async def override_db():
        yield patch_db

    async def override_user():
        return type("UserIdentity", (), {"id": 1})()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    app.include_router(project_file_router)

    response = TestClient(app).post(
        "/projects/2/files/22/patch",
        json=make_action(file_path="private.py").model_dump(),
    )

    assert response.status_code == 404