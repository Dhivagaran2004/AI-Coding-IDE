from dataclasses import dataclass

from App.models.repository_index import RepositoryIndex
from App.service.AI.index.repository_index_service import (
    RepositoryIndexService,
)


@dataclass
class FakeFile:
    id: int
    content: str | None
    type: str = "file"


class FakeQuery:
    def __init__(self, result=None):
        self.result = result

    def filter(self, *args):
        return self

    def first(self):
        return self.result


class FakeDB:
    def __init__(self, existing_index=None):
        self.existing_index = existing_index
        self.added = None
        self.committed = False
        self.refreshed = None

    def query(self, model):
        assert model is RepositoryIndex
        return FakeQuery(self.existing_index)

    def add(self, obj):
        self.added = obj

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        self.refreshed = obj


def test_new_file_can_be_indexed():
    db = FakeDB()

    service = RepositoryIndexService(
        db=db,
        project_id=1,
    )

    file = FakeFile(
        id=10,
        content="print('hello')",
    )

    index = service.create_or_update_index(file)

    assert index.project_id == 1
    assert index.file_id == 10
    assert index.status == "pending"
    assert index.indexed_content == "print('hello')"

    assert db.added is index
    assert db.committed is True
    assert db.refreshed is index


def test_changed_file_updates_repository_index():
    class ExistingIndex:
        project_id = 1
        file_id = 10
        content_hash = "old-hash"
        status = "ready"
        indexed_content = "old content"

    existing_index = ExistingIndex()

    db = FakeDB(
        existing_index=existing_index,
    )

    service = RepositoryIndexService(
        db=db,
        project_id=1,
    )

    file = FakeFile(
        id=10,
        content="new content",
    )

    index = service.create_or_update_index(file)

    assert index is existing_index
    assert index.file_id == 10
    assert index.project_id == 1

    assert index.content_hash == (
        service.calculate_content_hash("new content")
    )

    assert index.indexed_content == "new content"
    assert index.status == "pending"

    assert db.committed is True
    assert db.refreshed is index