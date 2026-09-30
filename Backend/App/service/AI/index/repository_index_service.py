import hashlib
import logging

from sqlalchemy.orm import Session

from App.models.project_file import ProjectFile
from App.models.repository_index import RepositoryIndex
from App.service.AI.index.vector_rag_service import VectorRAGService


logger = logging.getLogger(__name__)


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
    ) -> RepositoryIndex:
        """
        Create a new repository index record or update
        an existing one.
        """

        content_hash = self.calculate_content_hash(
            file.content
        )

        existing_index = self.get_index(file.id)

        if existing_index is None:
            existing_index = RepositoryIndex(
                project_id=self.project_id,
                file_id=file.id,
                content_hash=content_hash,
                status="pending",
                indexed_content=None,
            )
            self.db.add(existing_index)
        else:
            existing_index.status = "pending"

        try:
            self.db.commit()
            self.db.refresh(existing_index)

            indexed_content = file.content or ""
            existing_index.content_hash = content_hash
            existing_index.indexed_content = indexed_content
            existing_index.status = "ready"

            self.db.commit()
            self.db.refresh(existing_index)
            self._index_vectors(file)
            return existing_index
        except Exception:
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
                    )
                    self.db.add(failed_index)
                else:
                    failed_index.status = "failed"

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

    def index_project(self) -> dict[str, object]:
        """
        Index all indexable files in the project.

        Only new or changed files are processed.
        Unchanged files are skipped.
        """

        files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id
                == self.project_id,
                ProjectFile.type == "file",
            )
            .all()
        )

        indexed_count = 0
        skipped_count = 0
        vector_indexed_count = 0
        failed_count = 0

        indexed_files = []
        skipped_files = []
        failed_files = []

        for file in files:
            if self.needs_indexing(file):
                try:
                    self.create_or_update_index(file)
                except Exception as exc:
                    failed_count += 1
                    failed_files.append({
                        "file_id": file.id,
                        "error": str(exc),
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

    def _ensure_vectors(self, file: ProjectFile) -> int:
        if not isinstance(self.db, Session):
            return 0

        service = self.vector_index_service or VectorRAGService(
            db=self.db,
            project_id=self.project_id,
        )
        return service.ensure_file_indexed(file)