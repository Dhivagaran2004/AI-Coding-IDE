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

        if existing_index.status == "failed":
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
                indexed_content=file.content,
            )

            self.db.add(existing_index)

        else:
            existing_index.content_hash = content_hash
            existing_index.status = "pending"
            existing_index.indexed_content = file.content

        self.db.commit()
        self.db.refresh(existing_index)

        return existing_index

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

        indexed_files = []
        skipped_files = []

        for file in files:
            if self.needs_indexing(file):
                self.create_or_update_index(file)

                indexed_count += 1
                indexed_files.append(file.id)
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
        }