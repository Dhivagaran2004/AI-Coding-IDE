import pytest
from fastapi import HTTPException

from App.models.project import Project
from App.models.project_file import ProjectFile
from App.routes import project_file as project_file_router
from App.schema.project_file_schema import (
    ProjectFileCreate,
    ProjectFileUpdate,
)


class FakeProject:
    id = 1
    user_id = 1
    workspace_path = None


class FakeUser:
    id = 1


class FakeQuery:
    def __init__(self, result=None):
        self.result = result

    def filter(self, *args):
        return self

    def first(self):
        return self.result


class FakeDB:
    def __init__(self, file=None):
        self.file = file
        self.commits = 0

    def query(self, model):
        if model is Project:
            return FakeQuery(FakeProject())
        return FakeQuery(self.file)

    def add(self, item):
        self.file = item
        item.id = 10

    def commit(self):
        self.commits += 1

    def refresh(self, item):
        return None


class FakeIndexService:
    calls = []

    def __init__(self, db, project_id):
        self.db = db
        self.project_id = project_id

    def create_or_update_index(self, file):
        self.calls.append((self.project_id, file.id, file.content))

    def needs_indexing(self, file):
        return True

    def index_file_after_save(self, file, only_if_needed=False):
        if not only_if_needed or self.needs_indexing(file):
            self.create_or_update_index(file)


def test_created_files_are_indexed(monkeypatch):
    FakeIndexService.calls = []
    monkeypatch.setattr(
        project_file_router,
        "RepositoryIndexService",
        FakeIndexService,
    )
    monkeypatch.setattr(
        project_file_router,
        "_sync_workspace_file",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        project_file_router,
        "_assert_workspace_file_matches",
        lambda *args, **kwargs: None,
    )
    db = FakeDB()

    result = project_file_router.create_file_or_folder(
        project_id=1,
        file_data=ProjectFileCreate(
            name="main.py",
            type="file",
            content="print('hello')",
        ),
        db=db,
        current_user=FakeUser(),
    )

    assert isinstance(result, ProjectFile)
    assert FakeIndexService.calls == [(1, 10, "print('hello')")]


def test_updated_file_content_is_reindexed(monkeypatch):
    FakeIndexService.calls = []
    monkeypatch.setattr(
        project_file_router,
        "RepositoryIndexService",
        FakeIndexService,
    )
    monkeypatch.setattr(
        project_file_router,
        "_sync_workspace_file",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        project_file_router,
        "_assert_workspace_file_matches",
        lambda *args, **kwargs: None,
    )
    file = ProjectFile(
        id=10,
        project_id=1,
        name="main.py",
        type="file",
        content="print('old')",
    )
    db = FakeDB(file=file)

    result = project_file_router.update_project_file(
        project_id=1,
        file_id=10,
        file_data=ProjectFileUpdate(content="print('new')"),
        db=db,
        current_user=FakeUser(),
    )

    assert result.content == "print('new')"
    assert FakeIndexService.calls == [(1, 10, "print('new')")]


def test_stale_editor_content_is_rejected_before_update():
    file = ProjectFile(
        id=10,
        project_id=1,
        name="main.py",
        type="file",
        content="print('terminal edit')",
    )
    db = FakeDB(file=file)

    with pytest.raises(HTTPException) as error:
        project_file_router.update_project_file(
            project_id=1,
            file_id=10,
            file_data=ProjectFileUpdate(
                content="print('stale editor buffer')",
                expected_content="print('old editor buffer')",
            ),
            db=db,
            current_user=FakeUser(),
        )

    assert error.value.status_code == 409
    assert file.content == "print('terminal edit')"
