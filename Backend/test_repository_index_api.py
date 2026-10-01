from types import SimpleNamespace

from App.routes import project as project_routes
from App.routes import project_file as project_file_routes


class FakeQuery:
    def __init__(self, result=None, rows=None):
        self.result = result
        self.rows = rows or []

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.result

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self, project, project_file=None):
        self.project = project
        self.project_file = project_file
        self.added = None

    def query(self, model):
        if model.__name__ == "Project":
            return FakeQuery(self.project)
        return FakeQuery(self.project_file)

    def add(self, item):
        self.added = item
        if getattr(item, "id", None) is None:
            item.id = 99

    def commit(self):
        pass

    def refresh(self, item):
        pass


def test_create_file_triggers_repository_indexing(monkeypatch):
    project = SimpleNamespace(id=1, user_id=2)
    user = SimpleNamespace(id=2)
    db = FakeDB(project)
    indexed = []
    monkeypatch.setattr(
        project_file_routes,
        "update_repository_index",
        lambda db_arg, project_id, file: indexed.append((project_id, file)),
    )

    result = project_file_routes.create_file_or_folder(
        project_id=1,
        file_data=SimpleNamespace(
            parent_id=None,
            name="main.py",
            type="file",
            content="print('hi')",
            language="python",
        ),
        db=db,
        current_user=user,
    )

    assert result is db.added
    assert len(indexed) == 1
    assert indexed[0][0] == 1


def test_update_file_triggers_repository_indexing(monkeypatch):
    project = SimpleNamespace(id=1, user_id=2)
    file = SimpleNamespace(
        id=5,
        project_id=1,
        parent_id=None,
        name="main.py",
        type="file",
        content="old",
        language="python",
    )
    db = FakeDB(project, file)
    user = SimpleNamespace(id=2)
    indexed = []
    monkeypatch.setattr(
        project_file_routes,
        "update_repository_index",
        lambda db_arg, project_id, project_file: indexed.append(project_file),
    )

    result = project_file_routes.update_project_file(
        project_id=1,
        file_id=5,
        file_data=SimpleNamespace(
            name=None,
            content="new",
            language=None,
            parent_id=None,
        ),
        db=db,
        current_user=user,
    )

    assert result.content == "new"
    assert indexed == [file]


def test_project_index_endpoint_returns_service_report(monkeypatch):
    project = SimpleNamespace(id=1, user_id=2)
    db = FakeDB(project)
    report = {
        "project_id": 1,
        "total_files": 3,
        "indexed_count": 1,
        "skipped_count": 1,
        "indexed_files": [4],
        "skipped_files": [5],
        "failed_count": 1,
        "failed_files": [6],
        "errors": [{"file_id": 6, "error": "read failed"}],
    }

    class FakeIndexService:
        def __init__(self, db, project_id):
            assert db is not None
            assert project_id == 1

        def index_project(self):
            return report

    monkeypatch.setattr(
        project_routes,
        "RepositoryIndexService",
        FakeIndexService,
    )

    result = project_routes.index_project(
        project_id=1,
        db=db,
        current_user=SimpleNamespace(id=2),
    )

    assert result == report