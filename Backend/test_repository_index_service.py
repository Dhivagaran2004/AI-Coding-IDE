from dataclasses import dataclass

from App.models.repository_index import RepositoryIndex
from App.service.AI.index.repository_index_service import (
    RepositoryIndexService,
)


@dataclass
class FakeFile:
    id: int
    content: str | None
    name: str = "main.py"
    type: str = "file"


class FakeQuery:
    def __init__(self, result=None, rows=None):
        self.result = result
        self.rows = rows if rows is not None else ([result] if result else [])

    def filter(self, *args):
        return self

    def first(self):
        return self.result

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self, existing_index=None, files=None):
        self.existing_index = existing_index
        self.files = files or []
        self.added = None
        self.committed = False
        self.refreshed = None
        self.commit_states = []
        self.commit_count = 0
        self.fail_next_commit = False

    def query(self, model):
        if model is RepositoryIndex:
            return FakeQuery(self.existing_index)
        return FakeQuery(rows=self.files)

    def add(self, obj):
        self.added = obj
        if isinstance(obj, RepositoryIndex):
            self.existing_index = obj

    def delete(self, obj):
        if obj is self.existing_index:
            self.existing_index = None

    def commit(self):
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError("database commit failed")
        self.committed = True
        self.commit_count += 1
        if self.existing_index is not None:
            self.commit_states.append(self.existing_index.status)

    def rollback(self):
        pass

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
        status="ready",
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


def test_unchanged_ready_file_is_not_reindexed():
    content = "print('hello')"
    service = RepositoryIndexService(FakeDB(), project_id=1)
    content_hash = service.calculate_content_hash(content)
    existing = RepositoryIndex(
        project_id=1,
        file_id=10,
        content_hash=content_hash,
        status="ready",
        indexed_content=content,
    )
    db = FakeDB(existing_index=existing)
    service.db = db

    result = service.create_or_update_index(
        FakeFile(id=10, content=content)
    )

    assert result is existing
    assert db.commit_count == 0


def test_changed_file_transitions_through_pending_to_ready():
    old_content = "print('old')"
    db = FakeDB(
        existing_index=RepositoryIndex(
            project_id=1,
            file_id=10,
            content_hash="old-hash",
            status="ready",
            indexed_content=old_content,
        )
    )
    service = RepositoryIndexService(db, project_id=1)

    result = service.create_or_update_index(
        FakeFile(id=10, content="print('new')")
    )

    assert db.commit_states == ["pending", "ready"]
    assert result.status == "ready"
    assert result.indexed_content == "print('new')"


def test_failed_index_is_marked_failed_and_can_be_retried(monkeypatch):
    db = FakeDB()
    service = RepositoryIndexService(db, project_id=1)
    file = FakeFile(id=10, content="print('hello')")

    def fail_indexing(_file):
        raise RuntimeError("index conversion failed")

    monkeypatch.setattr(service, "_get_indexed_content", fail_indexing)

    try:
        service.create_or_update_index(file)
    except RuntimeError as exc:
        assert str(exc) == "index conversion failed"
    else:
        raise AssertionError("Expected indexing failure to propagate")

    assert db.added.status == "failed"
    assert service.needs_indexing(file) is True

    monkeypatch.setattr(
        service,
        "_get_indexed_content",
        lambda project_file: project_file.content or "",
    )
    result = service.create_or_update_index(file)

    assert result.status == "ready"
    assert result.indexed_content == file.content


def test_reindex_failure_preserves_previous_valid_content(monkeypatch):
    previous_content = "previous valid source"
    previous_hash = "previous-hash"
    existing = RepositoryIndex(
        project_id=1,
        file_id=10,
        content_hash=previous_hash,
        status="ready",
        indexed_content=previous_content,
    )
    db = FakeDB(existing_index=existing)
    service = RepositoryIndexService(db, project_id=1)
    monkeypatch.setattr(
        service,
        "_get_indexed_content",
        lambda _file: (_ for _ in ()).throw(RuntimeError("failed")),
    )

    try:
        service.create_or_update_index(
            FakeFile(id=10, content="new source")
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected indexing failure to propagate")

    assert existing.status == "failed"
    assert existing.content_hash == previous_hash
    assert existing.indexed_content == previous_content


def test_pending_commit_failure_is_recorded_as_failed():
    previous_content = "previous valid source"
    existing = RepositoryIndex(
        project_id=1,
        file_id=10,
        content_hash="previous-hash",
        status="ready",
        indexed_content=previous_content,
    )
    db = FakeDB(existing_index=existing)
    db.fail_next_commit = True
    service = RepositoryIndexService(db, project_id=1)

    try:
        service.create_or_update_index(
            FakeFile(id=10, content="new source")
        )
    except RuntimeError as exc:
        assert str(exc) == "database commit failed"
    else:
        raise AssertionError("Expected pending commit failure")

    assert existing.status == "failed"
    assert existing.content_hash == "previous-hash"
    assert existing.indexed_content == previous_content


def test_index_project_reports_successful_and_skipped_files():
    file = FakeFile(id=10, content="print('hello')")
    db = FakeDB(files=[file])

    result = RepositoryIndexService(db, project_id=1).index_project()

    assert result["project_id"] == 1
    assert result["total_files"] == 1
    assert result["indexed_count"] == 1
    assert result["skipped_count"] == 0
    assert result["indexed_files"] == [10]
    assert result["skipped_files"] == []
    assert result["failed_count"] == 0
    assert db.existing_index.status == "ready"


def test_index_project_reports_failure_details(monkeypatch):
    file = FakeFile(id=10, content="print('hello')")
    db = FakeDB(files=[file])
    service = RepositoryIndexService(db, project_id=1)
    monkeypatch.setattr(
        service,
        "_get_indexed_content",
        lambda _file: (_ for _ in ()).throw(RuntimeError("index failed")),
    )

    result = service.index_project()

    assert result["indexed_count"] == 0
    assert result["failed_count"] == 1
    assert result["failed_files"] == [10]
    assert result["errors"] == [
        {"file_id": 10, "error": "index failed"}
    ]
    assert db.existing_index.status == "failed"


def test_sensitive_file_is_skipped_from_indexing():
    file = FakeFile(id=10, content="secret", name=".env.production")
    db = FakeDB(files=[file])

    result = RepositoryIndexService(db, project_id=1).index_project()

    assert result["indexed_count"] == 0
    assert result["skipped_count"] == 1
    assert result["skipped_files"] == [10]
    assert db.existing_index is None

