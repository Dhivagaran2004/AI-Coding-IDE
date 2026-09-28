from dataclasses import dataclass

from App.models.repository_index import RepositoryIndex
from App.service.AI.index.repository_index_service import (
    RepositoryIndexService,
)


@dataclass
class FakeFile:
    id: int
    content: str | None


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
        return FakeQuery(self.existing_index)

    def add(self, obj):
        self.added = obj

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        self.refreshed = obj


def test_calculate_content_hash():
    db = FakeDB()
    service = RepositoryIndexService(
        db=db,
        project_id=1,
    )

    result = service.calculate_content_hash(
        "hello"
    )

    assert len(result) == 64
    assert result == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e"
        "1b161e5c1fa7425e73043362938b9824"
    )


def test_calculate_content_hash_empty_content():
    db = FakeDB()
    service = RepositoryIndexService(
        db=db,
        project_id=1,
    )

    result = service.calculate_content_hash(
        None
    )

    assert len(result) == 64
    assert result == (
        "e3b0c44298fc1c149afbf4c8996fb924"
        "27ae41e4649b934ca495991b7852b855"
    )


def test_new_file_needs_indexing():
    db = FakeDB(existing_index=None)

    service = RepositoryIndexService(
        db=db,
        project_id=1,
    )

    file = FakeFile(
        id=10,
        content="print('hello')",
    )

    assert service.needs_indexing(file) is True


def test_unchanged_file_does_not_need_indexing():
    content = "print('hello')"

    db = FakeDB()

    service = RepositoryIndexService(
        db=db,
        project_id=1,
    )

    content_hash = service.calculate_content_hash(
        content
    )

    db.existing_index = RepositoryIndex(
        project_id=1,
        file_id=10,
        content_hash=content_hash,
        status="indexed",
        indexed_content=content,
    )

    file = FakeFile(
        id=10,
        content=content,
    )

    assert service.needs_indexing(file) is False


def test_changed_file_needs_indexing():
    old_content = "print('hello')"

    db = FakeDB()

    service = RepositoryIndexService(
        db=db,
        project_id=1,
    )

    old_hash = service.calculate_content_hash(
        old_content
    )

    db.existing_index = RepositoryIndex(
        project_id=1,
        file_id=10,
        content_hash=old_hash,
        status="indexed",
        indexed_content=old_content,
    )

    file = FakeFile(
        id=10,
        content="print('hello world')",
    )

    assert service.needs_indexing(file) is True


def test_failed_index_needs_indexing():
    content = "print('hello')"

    db = FakeDB()

    service = RepositoryIndexService(
        db=db,
        project_id=1,
    )

    content_hash = service.calculate_content_hash(
        content
    )

    db.existing_index = RepositoryIndex(
        project_id=1,
        file_id=10,
        content_hash=content_hash,
        status="failed",
        indexed_content=content,
    )

    file = FakeFile(
        id=10,
        content=content,
    )

    assert service.needs_indexing(file) is True


def test_create_index():
    content = "print('hello')"

    db = FakeDB(existing_index=None)

    service = RepositoryIndexService(
        db=db,
        project_id=1,
    )

    file = FakeFile(
        id=10,
        content=content,
    )

    result = service.create_or_update_index(file)

    assert isinstance(result, RepositoryIndex)
    assert result.project_id == 1
    assert result.file_id == 10
    assert result.status == "pending"
    assert result.indexed_content == content

    assert db.added is result
    assert db.committed is True
    assert db.refreshed is result


def test_update_existing_index():
    old_content = "print('hello')"
    new_content = "print('hello world')"

    db = FakeDB()

    service = RepositoryIndexService(
        db=db,
        project_id=1,
    )

    old_hash = service.calculate_content_hash(
        old_content
    )

    existing_index = RepositoryIndex(
        project_id=1,
        file_id=10,
        content_hash=old_hash,
        status="indexed",
        indexed_content=old_content,
    )

    db.existing_index = existing_index

    file = FakeFile(
        id=10,
        content=new_content,
    )

    result = service.create_or_update_index(file)

    new_hash = service.calculate_content_hash(
        new_content
    )

    assert result is existing_index
    assert result.content_hash == new_hash
    assert result.status == "pending"
    assert result.indexed_content == new_content

    assert db.added is None
    assert db.committed is True
    assert db.refreshed is result

