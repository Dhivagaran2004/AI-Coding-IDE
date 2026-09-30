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
        self.commit_count = 0
        self.fail_on_commit = None
        self.snapshot = None
        self.refreshed = None

    def query(self, model):
        return FakeQuery(self.existing_index)

    def add(self, obj):
        self.added = obj
        self.existing_index = obj

    def commit(self):
        self.commit_count += 1
        if self.commit_count == self.fail_on_commit:
            raise RuntimeError("database write failed")

        self.committed = True
        if self.existing_index is not None:
            self.snapshot = (
                self.existing_index.content_hash,
                self.existing_index.status,
                self.existing_index.indexed_content,
            )

    def rollback(self):
        if self.existing_index is not None and self.snapshot is not None:
            (
                self.existing_index.content_hash,
                self.existing_index.status,
                self.existing_index.indexed_content,
            ) = self.snapshot

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
        status="ready",
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
        status="ready",
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
    assert result.status == "ready"
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
    assert result.status == "ready"
    assert result.indexed_content == new_content

    assert db.added is None
    assert db.committed is True
    assert db.refreshed is result


def test_index_failure_marks_record_failed_and_preserves_snapshot():
    old_content = "print('old')"
    new_content = "print('new')"
    db = FakeDB()
    service = RepositoryIndexService(db=db, project_id=1)
    existing_index = RepositoryIndex(
        project_id=1,
        file_id=10,
        content_hash=service.calculate_content_hash(old_content),
        status="ready",
        indexed_content=old_content,
    )
    db.existing_index = existing_index
    db.snapshot = (
        existing_index.content_hash,
        existing_index.status,
        existing_index.indexed_content,
    )
    db.fail_on_commit = 2

    try:
        service.create_or_update_index(
            FakeFile(id=10, content=new_content)
        )
    except RuntimeError as exc:
        assert str(exc) == "database write failed"
    else:
        raise AssertionError("Expected indexing failure")

    assert existing_index.status == "failed"
    assert existing_index.content_hash == service.calculate_content_hash(
        old_content
    )
    assert existing_index.indexed_content == old_content


def test_legacy_non_ready_status_needs_indexing():
    content = "print('hello')"
    db = FakeDB()
    service = RepositoryIndexService(db=db, project_id=1)
    db.existing_index = RepositoryIndex(
        project_id=1,
        file_id=10,
        content_hash=service.calculate_content_hash(content),
        status="indexed",
        indexed_content=content,
    )

    assert service.needs_indexing(
        FakeFile(id=10, content=content)
    ) is True

