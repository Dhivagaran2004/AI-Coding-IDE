from dataclasses import dataclass

import pytest

from App.models.project_file import ProjectFile
from App.models.repository_index import RepositoryIndex
from App.service.AI.index.indexed_context_service import (
    IndexedContextService,
)


@dataclass
class FakeFile:
    id: int
    project_id: int
    parent_id: int | None
    name: str
    type: str = "file"
    language: str | None = None


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self, indexes=None, files=None):
        self.indexes = indexes or []
        self.files = files or []

    def query(self, model):
        if model is RepositoryIndex:
            return FakeQuery(self.indexes)
        if model is ProjectFile:
            return FakeQuery(self.files)
        raise AssertionError(f"Unexpected model: {model}")


def make_index(file_id: int, content: str, status: str = "ready"):
    return RepositoryIndex(
        project_id=1,
        file_id=file_id,
        content_hash="hash",
        indexed_content=content,
        status=status,
    )


def test_empty_repository_returns_empty_context():
    service = IndexedContextService(FakeDB(), project_id=1)

    result = service.build_context()

    assert result.files == []
    assert result.content == ""
    assert result.file_count == 0
    assert result.character_count == 0


def test_only_ready_indexes_are_returned():
    files = [
        FakeFile(id=1, project_id=1, parent_id=None, name="ready.py"),
        FakeFile(id=2, project_id=1, parent_id=None, name="pending.py"),
    ]
    db = FakeDB(
        indexes=[
            make_index(1, "print('ready')"),
            make_index(2, "print('pending')", status="pending"),
        ],
        files=files,
    )

    result = IndexedContextService(db, project_id=1).build_context()

    assert [item.file_id for item in result.files] == [1]
    assert "ready.py" in result.content
    assert "pending.py" not in result.content


def test_pending_indexes_are_excluded():
    db = FakeDB(
        indexes=[make_index(1, "not ready", status="pending")],
        files=[FakeFile(id=1, project_id=1, parent_id=None, name="main.py")],
    )

    result = IndexedContextService(db, project_id=1).build_context()

    assert result.files == []
    assert result.content == ""


def test_nested_file_path_is_reconstructed():
    files = [
        FakeFile(id=10, project_id=1, parent_id=None, name="Backend", type="folder"),
        FakeFile(id=11, project_id=1, parent_id=10, name="App", type="folder"),
        FakeFile(id=12, project_id=1, parent_id=11, name="main.py"),
    ]
    db = FakeDB(indexes=[make_index(12, "print('ok')")], files=files)

    result = IndexedContextService(db, project_id=1).build_context()

    assert result.files[0].path == "Backend/App/main.py"


def test_language_is_detected_from_file_extension():
    db = FakeDB(
        indexes=[make_index(1, "print('ok')")],
        files=[FakeFile(id=1, project_id=1, parent_id=None, name="main.py")],
    )

    result = IndexedContextService(db, project_id=1).build_context()

    assert result.files[0].language == "python"
    assert "```python" in result.content


def test_maximum_file_limit_is_respected():
    files = [
        FakeFile(id=file_id, project_id=1, parent_id=None, name=f"{file_id}.txt")
        for file_id in range(1, 4)
    ]
    indexes = [make_index(file_id, f"content {file_id}") for file_id in range(1, 4)]

    result = IndexedContextService(
        FakeDB(indexes=indexes, files=files),
        project_id=1,
        max_files=2,
    ).build_context()

    assert len(result.files) == 2


def test_maximum_character_limit_is_respected():
    db = FakeDB(
        indexes=[make_index(1, "x" * 200)],
        files=[FakeFile(id=1, project_id=1, parent_id=None, name="large.txt")],
    )

    result = IndexedContextService(
        db,
        project_id=1,
        max_chars=80,
    ).build_context()

    assert result.content == ""
    assert result.character_count <= 80


def test_empty_indexed_content_is_skipped():
    db = FakeDB(
        indexes=[make_index(1, "   ")],
        files=[FakeFile(id=1, project_id=1, parent_id=None, name="empty.py")],
    )

    result = IndexedContextService(db, project_id=1).build_context()

    assert result.files == []
    assert result.content == ""


@pytest.mark.parametrize(
    "file_name",
    [
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "secret.png",
        "photo.jpg",
        "archive.zip",
        "program.exe",
        "module.pyc",
        "document.pdf",
    ],
)
def test_sensitive_and_binary_indexed_files_are_excluded(file_name):
    db = FakeDB(
        indexes=[make_index(1, "sensitive or binary contents")],
        files=[FakeFile(id=1, project_id=1, parent_id=None, name=file_name)],
    )

    result = IndexedContextService(db, project_id=1).build_context()

    assert result.files == []
    assert "sensitive or binary contents" not in result.content


def test_invalid_max_chars_is_rejected():
    with pytest.raises(ValueError, match="max_chars"):
        IndexedContextService(FakeDB(), project_id=1, max_chars=0)


def test_invalid_max_files_is_rejected():
    with pytest.raises(ValueError, match="max_files"):
        IndexedContextService(FakeDB(), project_id=1, max_files=0)