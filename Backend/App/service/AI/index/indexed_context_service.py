from dataclasses import dataclass

from sqlalchemy.orm import Session

from App.models.repository_index import RepositoryIndex


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
    """
    Retrieves repository context from the repository index.

    Only successfully indexed files are included.
    """

    def __init__(
        self,
        db: Session,
        project_id: int,
        max_chars: int = 30000,
        max_files: int = 8,
    ):
        if max_chars <= 0:
            raise ValueError(
                "max_chars must be greater than 0."
            )

        if max_files <= 0:
            raise ValueError(
                "max_files must be greater than 0."
            )

        self.db = db
        self.project_id = project_id
        self.max_chars = max_chars
        self.max_files = max_files

    def get_indexed_files(self) -> list[RepositoryIndex]:
        """
        Return successfully indexed files for the project.
        """

        return (
            self.db.query(RepositoryIndex)
            .filter(
                RepositoryIndex.project_id
                == self.project_id,
                RepositoryIndex.status == "ready",
            )
            .order_by(
                RepositoryIndex.file_id
            )
            .all()
        )

    def build_context(
        self,
    ) -> IndexedRepositoryContext:
        """
        Build AI-readable context from indexed files.
        """

        indexes = self.get_indexed_files()

        selected_files: list[IndexedContextFile] = []
        sections: list[str] = []
        character_count = 0

        for index in indexes:
            if len(selected_files) >= self.max_files:
                break

            content = index.indexed_content or ""

            if not content.strip():
                continue

            file_path = self._get_file_path(index)

            language = self._detect_language(
                file_path
            )

            section = self._format_file(
                path=file_path,
                content=content,
                language=language,
            )

            if (
                character_count + len(section)
                > self.max_chars
            ):
                continue

            selected_file = IndexedContextFile(
                file_id=index.file_id,
                path=file_path,
                content=content,
                language=language,
            )

            selected_files.append(
                selected_file
            )

            sections.append(section)

            character_count += len(section)

        repository_content = ""

        if sections:
            repository_content = (
                "## Indexed Repository Context\n\n"
                + "\n\n".join(sections)
            )

        return IndexedRepositoryContext(
            project_id=self.project_id,
            content=repository_content,
            files=selected_files,
            file_count=len(selected_files),
            character_count=len(repository_content),
        )

    def _get_file_path(
        self,
        index: RepositoryIndex,
    ) -> str:
        """
        Return the indexed file path.

        RepositoryIndex currently stores the file ID,
        so the ProjectFile table is used to obtain the
        file name and parent hierarchy.
        """

        from App.models.project_file import ProjectFile

        file = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.id == index.file_id,
                ProjectFile.project_id
                == self.project_id,
            )
            .first()
        )

        if file is None:
            return f"file-{index.file_id}"

        parts = [file.name]
        current_parent_id = file.parent_id

        while current_parent_id is not None:
            parent = (
                self.db.query(ProjectFile)
                .filter(
                    ProjectFile.id
                    == current_parent_id,
                    ProjectFile.project_id
                    == self.project_id,
                )
                .first()
            )

            if parent is None:
                break

            parts.append(parent.name)
            current_parent_id = parent.parent_id

        parts.reverse()

        return "/".join(parts)

    def _format_file(
        self,
        path: str,
        content: str,
        language: str | None,
    ) -> str:
        """
        Format a file as Markdown for the AI model.
        """

        language_name = language or ""

        return (
            f"### File: {path}\n"
            f"```{language_name}\n"
            f"{content}\n"
            f"```"
        )

    def _detect_language(
        self,
        path: str,
    ) -> str | None:
        """
        Detect a basic programming language from
        the file extension.
        """

        extension_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
            ".cs": "csharp",
            ".go": "go",
            ".rs": "rust",
            ".php": "php",
            ".rb": "ruby",
            ".html": "html",
            ".css": "css",
            ".scss": "scss",
            ".json": "json",
            ".xml": "xml",
            ".sql": "sql",
            ".sh": "shell",
            ".ps1": "powershell",
            ".md": "markdown",
        }

        path_lower = path.lower()

        for extension, language in extension_map.items():
            if path_lower.endswith(extension):
                return language

        return None