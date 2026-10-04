import hashlib
import logging
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from App.config import AI_INDEX_MAX_FILE_CHARS
from App.models.project_file import ProjectFile
from App.models.repository_index import RepositoryIndex
from App.models.repository_index_job import RepositoryIndexJob
from App.service.AI.context.context_cache import project_structure_cache
from App.service.AI.context.repository_context import RepositoryContextBuilder
from App.service.AI.index.code_chunker import CodeChunker
from App.service.AI.index.vector_rag_service import VectorRAGService


logger = logging.getLogger(__name__)


class IndexingAlreadyRunning(Exception):
    """Raised when a project already has an active indexing job."""


class RepositoryIndexService:
    """
    Manages repository file indexing.

    The service detects whether a file is new or has
    changed by comparing a SHA-256 content hash.
    """

    def __init__(
        self,
        db: Session,
        project_id: int,
        vector_index_service=None,
    ):
        self.db = db
        self.project_id = project_id
        self.vector_index_service = vector_index_service

    def calculate_content_hash(
        self,
        content: str | None,
    ) -> str:
        """
        Calculate a SHA-256 hash for file content.
        """

        normalized_content = content or ""

        return hashlib.sha256(
            normalized_content.encode("utf-8")
        ).hexdigest()

    def get_index(
        self,
        file_id: int,
    ) -> RepositoryIndex | None:
        """
        Return the existing index record for a file.
        """

        return (
            self.db.query(RepositoryIndex)
            .filter(
                RepositoryIndex.project_id
                == self.project_id,
                RepositoryIndex.file_id
                == file_id,
            )
            .first()
        )

    def needs_indexing(
        self,
        file: ProjectFile,
    ) -> bool:
        """
        Determine whether a file needs to be indexed.

        A file needs indexing when:
        - no index record exists
        - the content has changed
        - the previous index failed
        """

        existing_index = self.get_index(file.id)

        if existing_index is None:
            return True

        current_hash = self.calculate_content_hash(
            file.content
        )

        if existing_index.content_hash != current_hash:
            return True

        if existing_index.status != "ready":
            return True

        return False

    def create_or_update_index(
        self,
        file: ProjectFile,
    ) -> RepositoryIndex | None:
        """
        Create a new repository index record or update
        an existing one.
        """

        content_hash = self.calculate_content_hash(
            file.content
        )

        existing_index = self.get_index(file.id)
        try:
            if not self._is_indexable(file):
                if existing_index is not None:
                    self.db.delete(existing_index)
                    self.db.commit()
                self._delete_vectors([file.id])
                return None

            if existing_index is None:
                existing_index = RepositoryIndex(
                    project_id=self.project_id,
                    file_id=file.id,
                    content_hash=content_hash,
                    status="ready",
                    indexed_content=file.content or "",
                    error_message=None,
                )
                self.db.add(existing_index)
            else:
                existing_index.content_hash = content_hash
                existing_index.indexed_content = file.content or ""
                existing_index.status = "ready"
                existing_index.error_message = None
            self.db.commit()
            self.db.refresh(existing_index)
            project_structure_cache.invalidate_project(self.project_id)
            self._index_vectors(file)
            return existing_index
        except ValueError:
            raise
        except Exception as exc:
            self.db.rollback()
            try:
                failed_index = self.get_index(file.id)
                if failed_index is None:
                    failed_index = RepositoryIndex(
                        project_id=self.project_id,
                        file_id=file.id,
                        content_hash=content_hash,
                        status="failed",
                        indexed_content=None,
                        error_message=self._safe_error(exc),
                    )
                    self.db.add(failed_index)
                elif failed_index.status != "ready":
                    failed_index.status = "failed"
                    failed_index.error_message = self._safe_error(exc)
                else:
                    failed_index.error_message = self._safe_error(exc)
                self.db.commit()
                self.db.refresh(failed_index)
            except Exception:
                self.db.rollback()

            raise

    def _index_vectors(self, file: ProjectFile) -> None:
        try:
            service = self.vector_index_service
            if service is None:
                if not isinstance(self.db, Session):
                    return
                service = VectorRAGService(
                    db=self.db,
                    project_id=self.project_id,
                )

            service.index_file(file)
        except Exception as exc:
            logger.warning(
                "Vector indexing failed for project %s file %s; "
                "AI chat will use indexed-context fallback (%s).",
                self.project_id,
                file.id,
                type(exc).__name__,
            )

    @staticmethod
    def _safe_error(error: Exception) -> str:
        return f"Indexing failed ({type(error).__name__})."[:500]

    def _file_path(self, file: ProjectFile) -> str:
        if not hasattr(file, "name"):
            return f"file_{file.id}"
        builder = RepositoryContextBuilder(
            db=self.db,
            project_id=self.project_id,
        )
        return builder._get_file_path(file)

    def _is_indexable(self, file: ProjectFile) -> bool:
        content = file.content or ""
        path = self._file_path(file)
        if (
            not CodeChunker.is_indexable_path(path)
            or not content
            or len(content) > AI_INDEX_MAX_FILE_CHARS
            or "\x00" in content
        ):
            return False
        return bool(
            CodeChunker().chunk_file(
                project_id=self.project_id,
                file_id=file.id,
                file_path=path,
                language=getattr(file, "language", None),
                content=content,
            )
        )

    def _claim_job(
        self,
        allow_pending: bool = False,
    ) -> RepositoryIndexJob:
        job = (
            self.db.query(RepositoryIndexJob)
            .filter(RepositoryIndexJob.project_id == self.project_id)
            .with_for_update()
            .first()
        )
        if job is not None and (
            job.status == "running"
            or (job.status == "pending" and not allow_pending)
        ):
            raise IndexingAlreadyRunning

        now = datetime.utcnow()
        if job is None:
            job = RepositoryIndexJob(
                project_id=self.project_id,
                status="pending",
                error_message=None,
                started_at=None,
                completed_at=None,
                updated_at=now,
            )
            self.db.add(job)
            try:
                self.db.commit()
                self.db.refresh(job)
            except IntegrityError as exc:
                self.db.rollback()
                current_job = (
                    self.db.query(RepositoryIndexJob)
                    .filter(
                        RepositoryIndexJob.project_id == self.project_id
                    )
                    .first()
                )
                if current_job is not None and current_job.status in (
                    "pending",
                    "running",
                ):
                    raise IndexingAlreadyRunning from exc
                raise
        job.status = "running"
        job.error_message = None
        job.started_at = now
        job.completed_at = None
        job.updated_at = now
        self.db.commit()
        self.db.refresh(job)
        return job

    def _finish_job(
        self,
        job: RepositoryIndexJob,
        status: str,
        error_message: str | None = None,
    ) -> None:
        job.status = status
        job.error_message = error_message
        job.completed_at = datetime.utcnow()
        job.updated_at = job.completed_at
        self.db.commit()

    def get_job_state(self) -> dict[str, object]:
        job = (
            self.db.query(RepositoryIndexJob)
            .filter(RepositoryIndexJob.project_id == self.project_id)
            .first()
        )
        if job is None:
            return {
                "project_id": self.project_id,
                "status": "pending",
                "error_message": None,
                "started_at": None,
                "completed_at": None,
            }
        return {
            "project_id": self.project_id,
            "status": job.status,
            "error_message": job.error_message,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": (
                job.completed_at.isoformat() if job.completed_at else None
            ),
        }

    def queue_job(self) -> dict[str, object]:
        job = (
            self.db.query(RepositoryIndexJob)
            .filter(RepositoryIndexJob.project_id == self.project_id)
            .with_for_update()
            .first()
        )
        if job is not None and job.status in ("pending", "running"):
            raise IndexingAlreadyRunning
        now = datetime.utcnow()
        if job is None:
            job = RepositoryIndexJob(
                project_id=self.project_id,
                status="pending",
                updated_at=now,
            )
            self.db.add(job)
        else:
            job.status = "pending"
            job.error_message = None
            job.started_at = None
            job.completed_at = None
            job.updated_at = now
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise IndexingAlreadyRunning from exc
        return self.get_job_state()

    def index_project(self, queued: bool = False) -> dict[str, object]:
        """
        Index all indexable files in the project.

        Only new or changed files are processed.
        Unchanged files are skipped.
        """
        job = self._claim_job(allow_pending=queued)
        try:
            result = self._run_indexing()
            if result["failed_count"]:
                messages = [
                    item["error"]
                    for item in result["failed_files"]
                    if isinstance(item, dict)
                ]
                self._finish_job(
                    job,
                    "failed",
                    "; ".join(messages)[:2000] or "One or more files failed.",
                )
            else:
                self._finish_job(job, "ready")
            result["status"] = "failed" if result["failed_count"] else "ready"
            return result
        except Exception as exc:
            self.db.rollback()
            try:
                self._finish_job(job, "failed", self._safe_error(exc))
            except Exception:
                self.db.rollback()
            raise
        finally:
            project_structure_cache.invalidate_project(self.project_id)

    def _run_indexing(self) -> dict[str, object]:

        files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id
                == self.project_id,
                ProjectFile.type == "file",
            )
            .all()
        )

        file_ids = {file.id for file in files}
        stale_ids = [
            index.file_id
            for index in self.db.query(RepositoryIndex)
            .filter(RepositoryIndex.project_id == self.project_id)
            .all()
            if index.file_id not in file_ids
        ]
        stale_ids.extend(
            file.id for file in files if not self._is_indexable(file)
            and self.get_index(file.id) is not None
        )
        if stale_ids:
            self.db.query(RepositoryIndex).filter(
                RepositoryIndex.project_id == self.project_id,
                RepositoryIndex.file_id.in_(set(stale_ids)),
            ).delete(synchronize_session="fetch")
            self.db.commit()
            self._delete_vectors(stale_ids)

        indexed_count = 0
        skipped_count = 0
        vector_indexed_count = 0
        failed_count = 0

        indexed_files = []
        skipped_files = []
        failed_files = []

        for file in files:
            if not self._is_indexable(file):
                continue
            if self.needs_indexing(file):
                try:
                    self.create_or_update_index(file)
                except Exception as exc:
                    failed_count += 1
                    failed_files.append({
                        "file_id": file.id,
                        "error": self._safe_error(exc),
                    })
                else:
                    indexed_count += 1
                    indexed_files.append(file.id)
            else:
                skipped_count += 1
                skipped_files.append(file.id)
                try:
                    vector_indexed_count += self._ensure_vectors(file)
                except Exception as exc:
                    logger.warning(
                        "Vector backfill failed for project %s file %s "
                        "(%s).",
                        self.project_id,
                        file.id,
                        type(exc).__name__,
                    )

        return {
            "project_id": self.project_id,
            "total_files": len(files),
            "indexed_count": indexed_count,
            "skipped_count": skipped_count,
            "vector_indexed_count": vector_indexed_count,
            "failed_count": failed_count,
            "indexed_files": indexed_files,
            "skipped_files": skipped_files,
            "failed_files": failed_files,
        }

    def _delete_vectors(self, file_ids: list[int]) -> None:
        if not file_ids or not isinstance(self.db, Session):
            return
        try:
            service = self.vector_index_service or VectorRAGService(
                db=self.db,
                project_id=self.project_id,
            )
            service.delete_files(list(set(file_ids)))
        except Exception as exc:
            logger.warning(
                "Vector cleanup failed for project %s (%s).",
                self.project_id,
                type(exc).__name__,
            )

    def _ensure_vectors(self, file: ProjectFile) -> int:
        if not isinstance(self.db, Session):
            return 0

        service = self.vector_index_service or VectorRAGService(
            db=self.db,
            project_id=self.project_id,
        )
        return service.ensure_file_indexed(file)