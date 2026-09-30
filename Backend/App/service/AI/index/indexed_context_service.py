from dataclasses import dataclass
import hashlib

from sqlalchemy.orm import Session

from App.models.repository_index import RepositoryIndex
from App.service.AI.search.file_search import FileSearchResult


@dataclass
class IndexedContextFile:
    file_id: int
    path: str
    content: str
    language: str | None


@dataclass
class IndexedRepositoryContext:
    project_id: int
    content: str
    files: list[IndexedContextFile]
    file_count: int
    character_count: int


class IndexedContextService:
    """Build bounded AI context from ready index snapshots."""

    def __init__(
        self,
        db: Session,
        project_id: int,
        max_chars: int = 30000,
        max_files: int = 8,
    ):
        if max_chars <= 0:
            raise ValueError("max_chars must be greater than 0.")
        if max_files <= 0:
            raise ValueError("max_files must be greater than 0.")

        self.db = db
        self.project_id = project_id
        self.max_chars = max_chars
        self.max_files = max_files

    def get_indexed_files(
        self,
        file_ids: list[int],
    ) -> list[RepositoryIndex]:
        if not file_ids:
            return []

        return (
            self.db.query(RepositoryIndex)
            .filter(
                RepositoryIndex.project_id == self.project_id,
                RepositoryIndex.status == "ready",
                RepositoryIndex.file_id.in_(file_ids),
            )
            .order_by(RepositoryIndex.file_id)
            .all()
        )

    def build_context(
        self,
        candidates: list[FileSearchResult],
    ) -> IndexedRepositoryContext:
        indexes = {
            index.file_id: index
            for index in self.get_indexed_files(
                [candidate.file_id for candidate in candidates]
            )
            if (
                index.project_id == self.project_id
                and index.status == "ready"
            )
        }

        selected_files: list[IndexedContextFile] = []
        sections: list[str] = []
        context_header = "## Indexed Repository Context\n\n"

        for candidate in candidates:
            if len(selected_files) >= self.max_files:
                break

            index = indexes.get(candidate.file_id)
            if index is None or not index.indexed_content:
                continue

            current_hash = hashlib.sha256(
                (candidate.content or "").encode("utf-8")
            ).hexdigest()
            if index.content_hash != current_hash:
                continue

            section = self._format_file(
                path=candidate.path,
                content=index.indexed_content,
                language=candidate.language,
            )
            prospective_content = context_header + "\n\n".join(
                [*sections, section]
            )
            if len(prospective_content) > self.max_chars:
                continue

            selected_files.append(
                IndexedContextFile(
                    file_id=candidate.file_id,
                    path=candidate.path,
                    content=index.indexed_content,
                    language=candidate.language,
                )
            )
            sections.append(section)

        content = (
            context_header + "\n\n".join(sections)
            if sections
            else ""
        )

        return IndexedRepositoryContext(
            project_id=self.project_id,
            content=content,
            files=selected_files,
            file_count=len(selected_files),
            character_count=len(content),
        )

    @staticmethod
    def _format_file(
        path: str,
        content: str,
        language: str | None,
    ) -> str:
        language_tag = language or ""
        return (
            f"--- FILE: {path} ---\n"
            f"LANGUAGE: {language_tag}\n\n"
            f"```{language_tag}\n{content}\n```"
        )