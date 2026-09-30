from dataclasses import dataclass
import hashlib

from App.service.AI.index.indexed_context_service import (
    IndexedContextService,
)
from App.service.AI.search.file_search import FileSearchResult


@dataclass
class FakeIndex:
    file_id: int
    indexed_content: str | None
    content_hash: str
    status: str = "ready"
    project_id: int = 1


class FakeQuery:
    def __init__(self, values):
        self.values = values

    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return self.values


class FakeDB:
    def __init__(self, indexes):
        self.indexes = indexes

    def query(self, model):
        return FakeQuery(self.indexes)


def create_candidate(file_id=1, path="src/main.py", content="print('ok')"):
    return FileSearchResult(
        file_id=file_id,
        name=path.rsplit("/", 1)[-1],
        path=path,
        content=content,
        language="python",
        score=1.0,
    )


def create_index(file_id=1, content="print('ok')", status="ready"):
    content_hash = hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()
    return FakeIndex(
        file_id=file_id,
        indexed_content=content,
        content_hash=content_hash,
        status=status,
    )


def test_ready_snapshot_builds_bounded_repository_context():
    service = IndexedContextService(
        db=FakeDB([create_index()]),
        project_id=1,
    )

    result = service.build_context([create_candidate()])

    assert result.project_id == 1
    assert result.file_count == 1
    assert result.files[0].path == "src/main.py"
    assert "print('ok')" in result.content
    assert result.character_count == len(result.content)
    assert result.character_count <= 30000


def test_pending_and_stale_snapshots_are_not_used():
    current_content = "print('current')"
    indexes = [
        create_index(file_id=1, status="pending"),
        create_index(file_id=2),
    ]
    service = IndexedContextService(
        db=FakeDB(indexes),
        project_id=1,
    )

    result = service.build_context([
        create_candidate(file_id=1),
        create_candidate(file_id=2, content=current_content),
    ])

    assert result.files == []
    assert result.content == ""


def test_context_limits_files_and_characters():
    candidates = [
        create_candidate(file_id=file_id, content=f"print({file_id})")
        for file_id in range(1, 4)
    ]
    indexes = [
        create_index(file_id=file_id, content=f"print({file_id})")
        for file_id in range(1, 4)
    ]
    service = IndexedContextService(
        db=FakeDB(indexes),
        project_id=1,
        max_chars=170,
        max_files=2,
    )

    result = service.build_context(candidates)

    assert result.file_count <= 2
    assert result.character_count <= 170


def test_empty_candidate_list_returns_empty_context():
    service = IndexedContextService(
        db=FakeDB([]),
        project_id=1,
    )

    result = service.build_context([])

    assert result.content == ""
    assert result.files == []
    assert result.file_count == 0
    assert result.character_count == 0