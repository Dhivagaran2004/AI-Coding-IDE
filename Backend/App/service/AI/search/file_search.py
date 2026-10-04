from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from App.models.project_file import ProjectFile
from App.service.AI.index.code_chunker import CodeChunker


@dataclass
class FileSearchResult:
    """
    Represents one repository file returned
    by the file search service.
    """

    file_id: int
    name: str
    path: str
    content: str
    language: str | None
    score: float


class FileSearchService:
    """
    Searches project files using simple text matching.

    This is intentionally a lightweight implementation.

    Later Phase 8 tasks will introduce:
        - better relevance scoring
        - repository indexing
        - embeddings
        - vector search
        - RAG
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
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
        ".exe",
        ".dll",
        ".so",
        ".bin",
        ".pyc",
        ".class",
        ".o",
        ".obj",
        ".lock",
    }

    def __init__(
        self,
        db: Session,
        project_id: int,
    ):
        self.db = db
        self.project_id = project_id

    # =========================================================
    # PUBLIC SEARCH API
    # =========================================================

    def search(
        self,
        query: str,
        limit: int = 10,
    ) -> list[FileSearchResult]:
        """
        Search repository files for the given query.
        """

        query = query.strip()

        if not query:
            return []

        if limit <= 0:
            return []

        files = self._load_project_files()

        results: list[FileSearchResult] = []

        query_terms = self._tokenize(query)

        for project_file in files:

            if not self._should_include_file(
                project_file
            ):
                continue

            score = self._calculate_score(
                project_file,
                query,
                query_terms,
            )

            if score <= 0:
                continue

            path = self._get_file_path(
                project_file
            )

            results.append(
                FileSearchResult(
                    file_id=project_file.id,
                    name=project_file.name,
                    path=path,
                    content=project_file.content or "",
                    language=project_file.language,
                    score=score,
                )
            )

        results.sort(
            key=lambda result: (
                -result.score,
                result.path.lower(),
            )
        )

        return results[:limit]

    # =========================================================
    # LOAD FILES
    # =========================================================

    def _load_project_files(
        self,
    ) -> list[ProjectFile]:
        """
        Load all files belonging to the project.
        """

        return (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id
                == self.project_id
            )
            .order_by(
                ProjectFile.name.asc()
            )
            .all()
        )

    # =========================================================
    # FILE FILTERING
    # =========================================================

    def _should_include_file(
        self,
        project_file: ProjectFile,
    ) -> bool:
        """
        Ignore folders, secrets and binary files.
        """

        if project_file.type != "file":
            return False

        file_name = (
            project_file.name or ""
        ).strip()

        if not file_name:
            return False

        lower_name = file_name.lower()

        ignored_names = {
            name.lower()
            for name in self.IGNORED_FILE_NAMES
        }

        if lower_name in ignored_names:
            return False

        for extension in self.IGNORED_EXTENSIONS:
            if lower_name.endswith(extension):
                return False

        if not project_file.content:
            return False
        if CodeChunker.contains_sensitive_content(project_file.content):
            return False

        return True

    # =========================================================
    # TOKENIZATION
    # =========================================================

    @staticmethod
    def _tokenize(
        query: str,
    ) -> list[str]:
        """
        Convert a search query into lowercase
        searchable terms.
        """

        return [
            term
            for term in query.lower().split()
            if term
        ]

    # =========================================================
    # SCORE
    # =========================================================

    def _calculate_score(
        self,
        project_file: ProjectFile,
        query: str,
        query_terms: list[str],
    ) -> float:
        """
        Calculate a simple relevance score.

        Priority:

            exact file name
                ↓
            file path
                ↓
            content
        """

        file_name = (
            project_file.name or ""
        ).lower()

        path = self._get_file_path(
            project_file
        ).lower()

        content = (
            project_file.content or ""
        ).lower()

        query_lower = query.lower()

        score = 0.0

        # Exact filename match.
        if file_name == query_lower:
            score += 100.0

        # Filename contains complete query.
        if query_lower in file_name:
            score += 50.0

        # Path contains complete query.
        if query_lower in path:
            score += 30.0

        # Content contains complete query.
        if query_lower in content:
            score += 20.0

        # Individual terms.
        for term in query_terms:

            if term in file_name:
                score += 15.0

            if term in path:
                score += 10.0

            if term in content:
                score += 5.0

        return score

    # =========================================================
    # BUILD FILE PATH
    # =========================================================

    def _get_file_path(
        self,
        project_file: ProjectFile,
    ) -> str:
        """
        Build the complete repository path.
        """

        parts: list[str] = []

        current = project_file

        visited_ids: set[int] = set()

        while current is not None:

            current_id = getattr(
                current,
                "id",
                None,
            )

            if (
                current_id is not None
                and current_id in visited_ids
            ):
                break

            if current_id is not None:
                visited_ids.add(current_id)

            name = (
                current.name or ""
            ).strip()

            if name:
                parts.append(name)

            parent_id = getattr(
                current,
                "parent_id",
                None,
            )

            if parent_id is None:
                break

            current = self._find_file_by_id(
                parent_id
            )

        parts.reverse()

        return "/".join(parts)

    # =========================================================
    # FIND FILE
    # =========================================================

    def _find_file_by_id(
        self,
        file_id: int,
    ) -> ProjectFile | None:
        """
        Find a project file or folder by ID.
        """

        return (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.id == file_id,
                ProjectFile.project_id
                == self.project_id,
            )
            .first()
        )