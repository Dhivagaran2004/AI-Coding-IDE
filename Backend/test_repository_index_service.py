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

def test_index_project_empty_project(db_session):
    service = RepositoryIndexService(
        db=db_session,
        project_id=1,
    )

    result = service.index_project()

    assert result["project_id"] == 1
    assert result["total_files"] == 0
    assert result["indexed_count"] == 0
    assert result["skipped_count"] == 0
    assert result["indexed_files"] == []
    assert result["skipped_files"] == []


def test_index_project_indexes_multiple_files(
    db_session,
    project,
):
    file_one = ProjectFile(
        project_id=project.id,
        name="main.py",
        type="file",
        content="print('hello')",
        language="python",
    )

    file_two = ProjectFile(
        project_id=project.id,
        name="utils.py",
        type="file",
        content="def add(a, b):\n    return a + b",
        language="python",
    )

    db_session.add_all([file_one, file_two])
    db_session.commit()

    service = RepositoryIndexService(
        db=db_session,
        project_id=project.id,
    )

    result = service.index_project()

    assert result["total_files"] == 2
    assert result["indexed_count"] == 2
    assert result["skipped_count"] == 0

    assert set(result["indexed_files"]) == {
        file_one.id,
        file_two.id,
    }

    indexes = (
        db_session.query(RepositoryIndex)
        .filter(
            RepositoryIndex.project_id == project.id
        )
        .all()
    )

    assert len(indexes) == 2


def test_index_project_skips_unchanged_files(
    db_session,
    project,
):
    project_file = ProjectFile(
        project_id=project.id,
        name="main.py",
        type="file",
        content="print('hello')",
        language="python",
    )

    db_session.add(project_file)
    db_session.commit()

    service = RepositoryIndexService(
        db=db_session,
        project_id=project.id,
    )

    first_result = service.index_project()

    assert first_result["indexed_count"] == 1
    assert first_result["skipped_count"] == 0

    second_result = service.index_project()

    assert second_result["indexed_count"] == 0
    assert second_result["skipped_count"] == 1
    assert second_result["indexed_files"] == []
    assert second_result["skipped_files"] == [
        project_file.id
    ]


def test_index_project_indexes_changed_file(
    db_session,
    project,
):
    project_file = ProjectFile(
        project_id=project.id,
        name="main.py",
        type="file",
        content="print('hello')",
        language="python",
    )

    db_session.add(project_file)
    db_session.commit()

    service = RepositoryIndexService(
        db=db_session,
        project_id=project.id,
    )

    first_result = service.index_project()

    assert first_result["indexed_count"] == 1

    first_index = service.get_index(
        project_file.id
    )

    assert first_index is not None

    original_hash = first_index.content_hash

    project_file.content = "print('updated')"
    db_session.commit()

    second_result = service.index_project()

    assert second_result["indexed_count"] == 1
    assert second_result["skipped_count"] == 0
    assert second_result["indexed_files"] == [
        project_file.id
    ]

    second_index = service.get_index(
        project_file.id
    )

    assert second_index is not None
    assert second_index.content_hash != original_hash
    assert second_index.indexed_content == (
        "print('updated')"
    )


def test_index_project_ignores_folders(
    db_session,
    project,
):
    folder = ProjectFile(
        project_id=project.id,
        name="src",
        type="folder",
        content=None,
    )

    file = ProjectFile(
        project_id=project.id,
        name="main.py",
        type="file",
        content="print('hello')",
        language="python",
    )

    db_session.add_all([folder, file])
    db_session.commit()

    service = RepositoryIndexService(
        db=db_session,
        project_id=project.id,
    )

    result = service.index_project()

    assert result["total_files"] == 1
    assert result["indexed_count"] == 1
    assert result["skipped_count"] == 0

    assert result["indexed_files"] == [
        file.id
    ]

    folder_index = service.get_index(
        folder.id
    )

    assert folder_index is None


def test_index_project_mixed_file_states(
    db_session,
    project,
):
    unchanged_file = ProjectFile(
        project_id=project.id,
        name="unchanged.py",
        type="file",
        content="print('unchanged')",
        language="python",
    )

    changed_file = ProjectFile(
        project_id=project.id,
        name="changed.py",
        type="file",
        content="print('old')",
        language="python",
    )

    new_file = ProjectFile(
        project_id=project.id,
        name="new.py",
        type="file",
        content="print('new')",
        language="python",
    )

    db_session.add_all(
        [
            unchanged_file,
            changed_file,
        ]
    )
    db_session.commit()

    service = RepositoryIndexService(
        db=db_session,
        project_id=project.id,
    )

    service.index_project()

    changed_file.content = "print('updated')"

    db_session.add(new_file)
    db_session.commit()

    result = service.index_project()

    assert result["total_files"] == 3
    assert result["indexed_count"] == 2
    assert result["skipped_count"] == 1

    assert set(result["indexed_files"]) == {
        changed_file.id,
        new_file.id,
    }

    assert result["skipped_files"] == [
        unchanged_file.id
    ]