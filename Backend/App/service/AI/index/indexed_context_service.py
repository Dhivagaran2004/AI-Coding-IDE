from dataclasses import dataclass

from sqlalchemy.orm import Session

from App.models.project_file import ProjectFile
from App.models.repository_index import RepositoryIndex


@dataclass
class IndexedContextFile:
    file_id: int
    path: str
    language: str | None
    content: str


@dataclass
class IndexedRepositoryContext:
    project_id: int
    content: str
    files: list[IndexedContextFile]
    file_count: int
    character_count: int


class IndexedContextService:
    DEFAULT_MAX_FILES = 8
    DEFAULT_MAX_CHARS = 30000

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

    LANGUAGE_BY_EXTENSION = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".go": "go",
        ".rs": "rust",
        ".php": "php",
        ".rb": "ruby",
        ".swift": "swift",
        ".kt": "kotlin",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".sql": "sql",
        ".json": "json",
        ".xml": "xml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".sh": "bash",
        ".ps1": "powershell",
    }

    def __init__(
        self,
        db: Session,
        project_id: int,
        max_chars: int | None = None,
        max_files: int | None = None,
    ):
        self.db = db
        self.project_id = project_id
        self.max_chars = (
            max_chars
            if max_chars is not None
            else self.DEFAULT_MAX_CHARS
        )
        self.max_files = (
            max_files
            if max_files is not None
            else self.DEFAULT_MAX_FILES
        )

        if self.max_chars <= 0:
            raise ValueError("max_chars must be greater than zero.")
        if self.max_files <= 0:
            raise ValueError("max_files must be greater than zero.")

    def build_context(
        self,
        file_ids: list[int] | None = None,
    ) -> IndexedRepositoryContext:
        indexes = (
            self.db.query(RepositoryIndex)
            .filter(
                RepositoryIndex.project_id == self.project_id,
                RepositoryIndex.status == "ready",
            )
            .all()
        )
        files = (
            self.db.query(ProjectFile)
            .filter(ProjectFile.project_id == self.project_id)
            .all()
        )
        files_by_id = {
            project_file.id: project_file
            for project_file in files
        }
        allowed_ids = set(file_ids) if file_ids is not None else None

        ready_files: list[IndexedContextFile] = []
        for index in indexes:
            if index.status != "ready":
                continue
            if allowed_ids is not None and index.file_id not in allowed_ids:
                continue

            project_file = files_by_id.get(index.file_id)
            content = index.indexed_content or ""
            if project_file is None or not content.strip():
                continue
            if project_file.type != "file" or not self._is_safe_file(project_file.name):
                continue

            path = self._get_file_path(project_file, files_by_id)
            if not path:
                continue

            ready_files.append(
                IndexedContextFile(
                    file_id=project_file.id,
                    path=path,
                    language=(
                        project_file.language
                        or self._detect_language(project_file.name)
                    ),
                    content=content,
                )
            )

        ready_files.sort(key=lambda item: item.path.lower())
        selected: list[IndexedContextFile] = []
        sections: list[str] = []
        current_chars = len("INDEXED REPOSITORY CONTEXT\n\n")

        for indexed_file in ready_files:
            if len(selected) >= self.max_files:
                break
            section = self._format_file(indexed_file)
            if current_chars + len(section) > self.max_chars:
                continue
            selected.append(indexed_file)
            sections.append(section)
            current_chars += len(section)

        content = ""
        if selected:
            content = "INDEXED REPOSITORY CONTEXT\n\n" + "".join(sections).rstrip()

        return IndexedRepositoryContext(
            project_id=self.project_id,
            content=content,
            files=selected,
            file_count=len(selected),
            character_count=len(content),
        )

    @staticmethod
    def _is_safe_file(file_name: str | None) -> bool:
        lower_name = (file_name or "").strip().lower()
        if not lower_name:
            return False
        if lower_name in IndexedContextService.IGNORED_FILE_NAMES:
            return False
        return not any(
            lower_name.endswith(extension)
            for extension in IndexedContextService.IGNORED_EXTENSIONS
        )

    @staticmethod
    def _get_file_path(
        project_file: ProjectFile,
        files_by_id: dict[int, ProjectFile],
    ) -> str:
        parts: list[str] = []
        current = project_file
        visited: set[int] = set()

        while current is not None:
            if current.id in visited:
                return ""
            visited.add(current.id)
            name = (current.name or "").strip()
            if name:
                parts.append(name)
            if current.parent_id is None:
                break
            current = files_by_id.get(current.parent_id)

        parts.reverse()
        return "/".join(parts)

    @staticmethod
    def _detect_language(file_name: str | None) -> str | None:
        lower_name = (file_name or "").lower()
        for extension, language in IndexedContextService.LANGUAGE_BY_EXTENSION.items():
            if lower_name.endswith(extension):
                return language
        return None

    @staticmethod
    def _format_file(indexed_file: IndexedContextFile) -> str:
        language = indexed_file.language or ""
        return (
            f"--- FILE: {indexed_file.path} ---\n"
            f"LANGUAGE: {language}\n\n"
            f"```{language}\n{indexed_file.content}\n```\n\n"
        )