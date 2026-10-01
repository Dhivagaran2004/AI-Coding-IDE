import hashlib

from sqlalchemy.orm import Session

from App.models.project_file import ProjectFile
from App.models.repository_index import RepositoryIndex


class RepositoryIndexService:
    """
    Manages repository file indexing.

    The service detects whether a file is new or has
    changed by comparing a SHA-256 content hash.
    """

    IGNORED_FILE_NAMES = {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "id_rsa",
        "id_rsa.pub",
    }

    IGNORED_EXTENSIONS = {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
        ".pdf", ".zip", ".tar", ".gz", ".7z", ".exe", ".dll",
        ".so", ".bin", ".pyc", ".class", ".o", ".obj", ".lock",
    }

    def __init__(
        self,
        db: Session,
        project_id: int,
    ):
        self.db = db
        self.project_id = project_id

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

    def remove_index(self, file_id: int) -> None:
        existing_index = self.get_index(file_id)
        if existing_index is None:
            return

        self.db.delete(existing_index)
        self.db.commit()

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

        if existing_index.status not in {"ready", "indexed"}:
            return True

        return False

    @classmethod
    def should_index_file(cls, file: ProjectFile) -> bool:
        if file.type != "file":
            return False

        file_name = (file.name or "").strip().lower()
        if not file_name or file_name in cls.IGNORED_FILE_NAMES:
            return False

        return not any(
            file_name.endswith(extension)
            for extension in cls.IGNORED_EXTENSIONS
        )

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

        if (
            existing_index is not None
            and existing_index.status in {"ready", "indexed"}
            and existing_index.content_hash == content_hash
        ):
            return existing_index

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

        previous_hash = existing_index.content_hash
        previous_content = existing_index.indexed_content

        try:
            self.db.commit()
            indexed_content = self._get_indexed_content(file)
            existing_index.content_hash = content_hash
            existing_index.indexed_content = indexed_content
            existing_index.status = "ready"
            self.db.commit()
            self.db.refresh(existing_index)
        except Exception:
            self.db.rollback()
            if self.get_index(file.id) is None:
                self.db.add(existing_index)
            existing_index.content_hash = previous_hash
            existing_index.indexed_content = previous_content
            existing_index.status = "failed"
            self.db.commit()
            raise

        return existing_index

    @staticmethod
    def _get_indexed_content(file: ProjectFile) -> str:
        return file.content or ""

    def index_project(self) -> dict:
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
        failed_count = 0

        indexed_files = []
        skipped_files = []
        failed_files = []
        errors = []

        for file in files:
            if not self.should_index_file(file):
                self.remove_index(file.id)
                skipped_count += 1
                skipped_files.append(file.id)
                continue

            if self.needs_indexing(file):
                try:
                    self.create_or_update_index(file)
                    indexed_count += 1
                    indexed_files.append(file.id)
                except Exception as exc:
                    failed_count += 1
                    failed_files.append(file.id)
                    errors.append({
                        "file_id": file.id,
                        "error": str(exc),
                    })
            else:
                skipped_count += 1
                skipped_files.append(file.id)

        return {
            "project_id": self.project_id,
            "total_files": len(files),
            "indexed_count": indexed_count,
            "skipped_count": skipped_count,
            "indexed_files": indexed_files,
            "skipped_files": skipped_files,
            "failed_count": failed_count,
            "failed_files": failed_files,
            "errors": errors,
        }