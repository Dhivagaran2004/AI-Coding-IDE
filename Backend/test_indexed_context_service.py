from dataclasses import dataclass

from App.service.AI.index.indexed_context_service import (
    IndexedContextService,
)


@dataclass
class FakeIndex:
    file_id: int
    indexed_content: str | None
    status: str = "ready"


@dataclass
class FakeFile:
    id: int
    name: str
    parent_id: int | None = None


class FakeQuery:
    def __init__(self, results):
        self.results = results

    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return self.results

    def first(self):
        if not self.results:
            return None

        return self.results[0]


class FakeDB:
    def __init__(
        self,
        indexes=None,
        files=None,
    ):
        self.indexes = indexes or []
        self.files = files or []

    def query(self, model):
        model_name = getattr(
            model,
            "__name__",
            "",
        )

        if model_name == "RepositoryIndex":
            return FakeQuery(self.indexes)

        if model_name == "ProjectFile":
            return FakeQuery(self.files)

        raise AssertionError(
            f"Unexpected model: {model}"
        )


def test_empty_repository_returns_empty_context():
    db = FakeDB()

    service = IndexedContextService(
        db=db,
        project_id=1,
    )

    result = service.build_context()

    assert result.project_id == 1
    assert result.content == ""
    assert result.files == []
    assert result.file_count == 0
    assert result.character_count == 0


def test_only_ready_indexes_are_used():
    indexes = [
        FakeIndex(
            file_id=1,
            indexed_content="print('ready')",
            status="ready",
        ),
        FakeIndex(
            file_id=2,
            indexed_content="print('pending')",
            status="pending",
        ),
    ]

    files = [
        FakeFile(
            id=1,
            name="main.py",
        ),
        FakeFile(
            id=2,
            name="pending.py",
        ),
    ]

    db = FakeDB(
        indexes=indexes[:1],
        files=files[:1],
    )

    service = IndexedContextService(
        db=db,
        project_id=1,
    )

    result = service.build_context()

    assert result.file_count == 1
    assert len(result.files) == 1
    assert result.files[0].path == "main.py"
    assert "print('ready')" in result.content
    assert "print('pending')" not in result.content


def test_nested_file_path_is_reconstructed():
    indexes = [
        FakeIndex(
            file_id=3,
            indexed_content="router = APIRouter()",
        )
    ]

    files = [
        FakeFile(
            id=3,
            name="router.py",
            parent_id=2,
        ),
        FakeFile(
            id=2,
            name="api",
            parent_id=1,
        ),
        FakeFile(
            id=1,
            name="App",
            parent_id=None,
        ),
    ]

    db = FakeDB(
        indexes=indexes,
        files=files,
    )

    service = IndexedContextService(
        db=db,
        project_id=1,
    )

    result = service.build_context()

    assert result.file_count == 1
    assert result.files[0].path == (
        "App/api/router.py"
    )

    assert "App/api/router.py" in result.content


def test_language_is_detected():
    indexes = [
        FakeIndex(
            file_id=1,
            indexed_content="def hello():\n    pass",
        )
    ]

    files = [
        FakeFile(
            id=1,
            name="hello.py",
        )
    ]

    db = FakeDB(
        indexes=indexes,
        files=files,
    )

    service = IndexedContextService(
        db=db,
        project_id=1,
    )

    result = service.build_context()

    assert result.files[0].language == "python"
    assert "```python" in result.content


def test_max_files_is_respected():
    indexes = [
        FakeIndex(
            file_id=1,
            indexed_content="file one",
        ),
        FakeIndex(
            file_id=2,
            indexed_content="file two",
        ),
        FakeIndex(
            file_id=3,
            indexed_content="file three",
        ),
    ]

    files = [
        FakeFile(
            id=1,
            name="one.py",
        ),
        FakeFile(
            id=2,
            name="two.py",
        ),
        FakeFile(
            id=3,
            name="three.py",
        ),
    ]

    db = FakeDB(
        indexes=indexes,
        files=files,
    )

    service = IndexedContextService(
        db=db,
        project_id=1,
        max_files=2,
    )

    result = service.build_context()

    assert result.file_count == 2
    assert len(result.files) == 2


def test_empty_content_is_skipped():
    indexes = [
        FakeIndex(
            file_id=1,
            indexed_content="",
        ),
        FakeIndex(
            file_id=2,
            indexed_content="valid content",
        ),
    ]

    files = [
        FakeFile(
            id=1,
            name="empty.py",
        ),
        FakeFile(
            id=2,
            name="valid.py",
        ),
    ]

    db = FakeDB(
        indexes=indexes,
        files=files,
    )

    service = IndexedContextService(
        db=db,
        project_id=1,
    )

    result = service.build_context()

    assert result.file_count == 1
    assert result.files[0].path == "valid.py"
    assert "valid content" in result.content


def test_invalid_max_chars_is_rejected():
    db = FakeDB()

    try:
        IndexedContextService(
            db=db,
            project_id=1,
            max_chars=0,
        )
        assert False
    except ValueError as exc:
        assert str(exc) == (
            "max_chars must be greater than 0."
        )


def test_invalid_max_files_is_rejected():
    db = FakeDB()

    try:
        IndexedContextService(
            db=db,
            project_id=1,
            max_files=0,
        )
        assert False
    except ValueError as exc:
        assert str(exc) == (
            "max_files must be greater than 0."
        )