from datetime import datetime
from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from App.database.database import Base
from App.models.project import Project
from App.models.project_file import ProjectFile
from App.models.repository_index import RepositoryIndex
from App.models.repository_index_job import RepositoryIndexJob
from App.models.user import User
from App.service.AI.index.repository_index_service import (
    IndexingAlreadyRunning,
    RepositoryIndexService,
)


class NoVectorIndexService:
    def index_file(self, file: ProjectFile) -> int:
        return 0

    def ensure_file_indexed(self, file: ProjectFile) -> int:
        return 0

    def delete_files(self, file_ids: list[int]) -> None:
        return None


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        User(
            id=901,
            name="Index Owner",
            email="index-owner@example.test",
            password_hash="hash",
            created_at=datetime.now(),
        )
    )
    session.flush()
    session.add(
        Project(id=901, user_id=901, name="Index Project", language="python")
    )
    session.commit()
    yield session
    session.close()
    engine.dispose()


def add_file(db: Session, name: str, content: str) -> ProjectFile:
    file = ProjectFile(
        project_id=901,
        name=name,
        type="file",
        content=content,
        language="python",
    )
    db.add(file)
    db.commit()
    db.refresh(file)
    return file


def service(db: Session) -> RepositoryIndexService:
    return RepositoryIndexService(
        db,
        901,
        vector_index_service=NoVectorIndexService(),
    )


def test_incremental_index_skips_unchanged_and_reindexes_changed_files(db):
    file = add_file(db, "main.py", "print('first')")
    first = service(db).index_project()
    second = service(db).index_project()

    assert first["indexed_count"] == 1
    assert second["indexed_count"] == 0
    assert second["skipped_count"] == 1

    file.content = "print('updated')"
    db.commit()
    third = service(db).index_project()

    assert third["indexed_count"] == 1
    indexed = db.query(RepositoryIndex).filter(
        RepositoryIndex.project_id == 901,
        RepositoryIndex.file_id == file.id,
    ).one()
    assert indexed.indexed_content == "print('updated')"
    assert indexed.status == "ready"


def test_deleted_files_are_removed_from_indexed_retrieval(db):
    file = add_file(db, "removed.py", "print('remove me')")
    service(db).index_project()
    db.delete(file)
    db.commit()

    result = service(db).index_project()

    assert result["indexed_count"] == 0
    assert db.query(RepositoryIndex).filter(
        RepositoryIndex.project_id == 901
    ).count() == 0


def test_failed_reindex_preserves_previous_ready_snapshot(db, monkeypatch):
    file = add_file(db, "main.py", "print('good snapshot')")
    service(db).index_project()
    file.content = "print('replacement')"
    db.commit()
    indexer = service(db)

    def fail_index(_file):
        raise RuntimeError("provider path must not be exposed")

    monkeypatch.setattr(indexer, "create_or_update_index", fail_index)
    result = indexer.index_project()
    indexed = db.query(RepositoryIndex).filter(
        RepositoryIndex.project_id == 901,
        RepositoryIndex.file_id == file.id,
    ).one()
    job = db.query(RepositoryIndexJob).filter(
        RepositoryIndexJob.project_id == 901
    ).one()

    assert result["failed_count"] == 1
    assert indexed.indexed_content == "print('good snapshot')"
    assert indexed.status == "ready"
    assert job.status == "failed"
    assert "provider path" not in job.error_message


def test_conflicting_index_job_is_rejected_and_failed_job_can_retry(db):
    db.add(RepositoryIndexJob(project_id=901, status="running"))
    db.commit()
    with pytest.raises(IndexingAlreadyRunning):
        service(db).index_project()

    job = db.query(RepositoryIndexJob).filter(
        RepositoryIndexJob.project_id == 901
    ).one()
    job.status = "failed"
    db.commit()

    result = service(db).index_project()
    assert result["status"] == "ready"
    assert job.status == "ready"


def test_background_job_has_pending_running_ready_lifecycle(db):
    indexer = service(db)
    queued = indexer.queue_job()
    assert queued["status"] == "pending"

    result = indexer.index_project(queued=True)

    assert result["status"] == "ready"
    assert indexer.get_job_state()["status"] == "ready"


def test_indexing_excludes_secret_and_oversized_files(db):
    secret = add_file(db, ".env", "API_KEY=should-not-be-indexed")
    large = add_file(db, "large.py", "x" * 500001)
    allowed = add_file(db, "source.py", "print('safe')")

    result = service(db).index_project()
    indexed_file_ids = {
        row.file_id
        for row in db.query(RepositoryIndex)
        .filter(RepositoryIndex.project_id == 901)
        .all()
    }

    assert result["indexed_count"] == 1
    assert indexed_file_ids == {allowed.id}
    assert secret.id not in indexed_file_ids
    assert large.id not in indexed_file_ids
