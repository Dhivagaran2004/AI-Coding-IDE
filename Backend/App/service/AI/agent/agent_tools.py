import re
from pathlib import PurePosixPath
from typing import cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from App.models.project import Project
from App.models.project_file import ProjectFile
from App.service.AI.context.repository_relevance import RepositoryRelevanceService
from App.service.AI.index.code_chunker import CodeChunker
from App.service.AI.index.vector_rag_service import VectorRAGService


class AgentToolError(ValueError):
    pass


class AgentToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListFilesArguments(AgentToolArguments):
    path: str = Field(default="", max_length=2000)


class ReadFileArguments(AgentToolArguments):
    path: str = Field(min_length=1, max_length=2000)


class CreateFileArguments(AgentToolArguments):
    path: str = Field(min_length=1, max_length=2000)
    content: str = Field(max_length=20000)


class UpdateFileArguments(AgentToolArguments):
    path: str = Field(min_length=1, max_length=2000)
    content: str = Field(max_length=20000)


class DeleteFileArguments(AgentToolArguments):
    path: str = Field(min_length=1, max_length=2000)


class ExecuteCommandArguments(AgentToolArguments):
    command: str = Field(min_length=1, max_length=500)


class GetExecutionResultArguments(AgentToolArguments):
    pass


class AgentTools:
    MAX_INSPECT_CHARS = 25000
    MAX_SEARCH_RESULTS = 20
    SEARCH_STOP_WORDS = {
        "a",
        "add",
        "an",
        "and",
        "build",
        "change",
        "create",
        "for",
        "generate",
        "implement",
        "in",
        "make",
        "of",
        "on",
        "please",
        "the",
        "to",
        "update",
        "using",
        "with",
    }
    SECRET_NAME_MARKERS = ("secret", "credential", "password", "private_key")
    PYTHON_IMPORT = re.compile(
        r"(?m)^\s*(?:from\s+([\w.]+)\s+import\s+([\w*., ]+)|"
        r"import\s+([\w.]+))"
    )
    MODULE_IMPORT = re.compile(
        r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*)["']([^"']+)["']"""
    )

    def __init__(self, db: Session, project_id: int, user_id: int):
        self.db = db
        self.project_id = project_id
        project = (
            db.query(Project)
            .filter(
                Project.id == project_id,
                Project.user_id == user_id,
            )
            .first()
        )
        if project is None:
            raise AgentToolError("Project not found.")

    def list_project_files(self) -> list[str]:
        files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == self.project_id,
                ProjectFile.type == "file",
            )
            .order_by(ProjectFile.id.asc())
            .limit(1000)
            .all()
        )
        result = []
        for project_file in files:
            path = self.get_file_path(project_file)
            if self.is_safe_path(path):
                result.append(path)
        return result

    def list_project_entries(self, directory: str = "") -> list[dict[str, str]]:
        normalized_directory = ""
        if directory not in {"", "."}:
            normalized_directory = self._validate_path(directory)
            folder = (
                self.db.query(ProjectFile)
                .filter(
                    ProjectFile.project_id == self.project_id,
                    ProjectFile.type == "folder",
                )
                .all()
            )
            if not any(
                self.get_file_path(item) == normalized_directory
                and self.is_safe_path(normalized_directory)
                for item in folder
            ):
                raise AgentToolError("Directory not found in this project.")

        entries: list[dict[str, str]] = []
        items = (
            self.db.query(ProjectFile)
            .filter(ProjectFile.project_id == self.project_id)
            .order_by(ProjectFile.id.asc())
            .all()
        )
        for item in items:
            path = self.get_file_path(item)
            if not self.is_safe_path(path):
                continue
            parent_path = PurePosixPath(path).parent.as_posix()
            if parent_path == ".":
                parent_path = ""
            if parent_path != normalized_directory:
                continue
            entries.append({
                "path": path,
                "type": cast(str, item.type),
            })
        return entries

    def inspect_file(self, file_path: str) -> dict[str, object]:
        project_file = self.resolve_file(file_path)
        content = cast(str | None, project_file.content) or ""
        if len(content) > self.MAX_INSPECT_CHARS:
            raise AgentToolError("File exceeds the agent inspection limit.")
        if self.contains_sensitive_content(content):
            raise AgentToolError("File content is protected from agent access.")
        return {
            "path": file_path,
            "language": cast(str | None, project_file.language),
            "content": content,
            "size": len(content.encode("utf-8")),
            "line_count": len(content.splitlines()),
        }

    def search_project(self, query: str) -> list[dict[str, object]]:
        terms = [
            term[:80]
            for term in re.findall(r"[A-Za-z0-9_]+", query.casefold())
            if len(term) > 1 and term not in self.SEARCH_STOP_WORDS
        ][:8]
        if not terms:
            return []
        files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == self.project_id,
                ProjectFile.type == "file",
            )
            .order_by(ProjectFile.id.asc())
            .limit(1000)
            .all()
        )
        matches: list[tuple[int, dict[str, object]]] = []
        for project_file in files:
            path = self.get_file_path(project_file)
            content = cast(str | None, project_file.content) or ""
            if not self.is_safe_path(path) or self.contains_sensitive_content(content):
                continue
            folded_path = path.casefold()
            folded_content = content.casefold()
            path_terms = [
                term for term in terms if term in folded_path
            ]
            content_terms = [
                term for term in terms if term in folded_content
            ]
            matching_terms = [
                term for term in terms
                if term in path_terms or term in content_terms
            ]
            if not matching_terms:
                continue
            score = len(path_terms) * 3 + len(content_terms)
            if "python" in terms and folded_path.endswith(".py"):
                score += 2
            content_lines = content.splitlines()
            line_number = next(
                (
                    index + 1
                    for index, line in enumerate(content_lines)
                    if any(term in line.casefold() for term in content_terms)
                ),
                0,
            )
            start = max(0, line_number - 3)
            snippet = "\n".join(content_lines[start:start + 6])[:1500]
            matches.append(
                (
                    score,
                    {
                        "path": path,
                        "line": line_number or None,
                        "snippet": snippet,
                        "matched_terms": matching_terms,
                    },
                )
            )
        matches.sort(key=lambda match: match[0], reverse=True)
        return [
            result
            for _, result in matches[:self.MAX_SEARCH_RESULTS]
        ]

    def find_file_relationships(
        self,
        file_paths: list[str],
        max_relationships: int = 12,
    ) -> list[tuple[str, str]]:
        relevant_paths = set(file_paths)
        if not relevant_paths:
            return []
        files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == self.project_id,
                ProjectFile.type == "file",
            )
            .order_by(ProjectFile.id.asc())
            .all()
        )
        safe_files = [
            (self.get_file_path(project_file), project_file)
            for project_file in files
            if self.is_safe_path(self.get_file_path(project_file))
        ]
        available_paths = {path for path, _ in safe_files}
        relationships: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for source_path, project_file in safe_files:
            content = cast(str | None, project_file.content) or ""
            if (
                len(content) > self.MAX_INSPECT_CHARS
                or self.contains_sensitive_content(content)
            ):
                continue
            references: set[str] = set()
            for match in self.PYTHON_IMPORT.finditer(content):
                module = match.group(1) or match.group(3)
                imported_names = match.group(2)
                if module:
                    references.add(module)
                if imported_names:
                    references.update(
                        name.strip().split(" as ", 1)[0]
                        for name in imported_names.split(",")
                        if name.strip() and name.strip() != "*"
                    )
            references.update(
                match.group(1) for match in self.MODULE_IMPORT.finditer(content)
            )

            for reference in references:
                normalized = reference.replace("\\", "/")
                if normalized.startswith("."):
                    normalized = PurePosixPath(
                        source_path
                    ).parent.joinpath(normalized).as_posix()
                elif "/" not in normalized and normalized.rsplit(".", 1)[-1] not in {
                    "py", "js", "jsx", "ts", "tsx", "java",
                }:
                    normalized = normalized.replace(".", "/")
                for extension in (".py", ".js", ".jsx", ".ts", ".tsx", ".java"):
                    normalized = normalized.removesuffix(extension)
                normalized = normalized.strip("./").casefold()
                candidates = []
                for path in sorted(available_paths):
                    target_stem = path.casefold()
                    for extension in (".py", ".js", ".jsx", ".ts", ".tsx", ".java"):
                        target_stem = target_stem.removesuffix(extension)
                    if (
                        target_stem == normalized
                        or target_stem.endswith(f"/{normalized}")
                        or target_stem.rsplit("/", 1)[-1] == normalized.rsplit("/", 1)[-1]
                    ):
                        candidates.append(path)
                for target_path in candidates:
                    if source_path == target_path:
                        continue
                    if source_path not in relevant_paths and target_path not in relevant_paths:
                        continue
                    relationship = (source_path, target_path)
                    if relationship not in seen:
                        relationships.append(relationship)
                        seen.add(relationship)
                        if len(relationships) >= max_relationships:
                            return relationships
        return relationships

    def retrieve_context(self, query: str) -> str:
        relevance = RepositoryRelevanceService(
            db=self.db,
            project_id=self.project_id,
            max_chars=20000,
            max_files=6,
        ).build_context(query=query)
        context_sections = []
        context_size = 0
        for result in relevance.search_results:
            if (
                not self.is_safe_path(result.path)
                or self.contains_sensitive_content(result.content)
            ):
                continue
            section = f"FILE: {result.path}\n{result.content[:6000]}"
            if context_size + len(section) > 20000:
                break
            context_sections.append(section)
            context_size += len(section)
        vector_result = VectorRAGService(
            db=self.db,
            project_id=self.project_id,
        ).search_project_context(
            project_id=self.project_id,
            query=query,
            top_k=5,
        )
        vector_sections = []
        vector_size = 0
        for chunk in vector_result.chunks:
            if (
                not self.is_safe_path(chunk.file_path)
                or self.contains_sensitive_content(chunk.content)
            ):
                continue
            section = (
                f"FILE: {chunk.file_path} LINES: {chunk.start_line}-{chunk.end_line}\n"
                f"{chunk.content[:6000]}"
            )
            if vector_size + len(section) > 20000:
                break
            vector_sections.append(section)
            vector_size += len(section)
        return "\n\n".join(vector_sections or context_sections)[:20000]

    def resolve_file(self, file_path: str) -> ProjectFile:
        path = self._validate_path(file_path)
        if not self.is_safe_path(path):
            raise AgentToolError("This file is protected from agent access.")
        files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == self.project_id,
                ProjectFile.type == "file",
            )
            .all()
        )
        for project_file in files:
            if self.get_file_path(project_file) == path:
                return project_file
        raise AgentToolError("File not found in this project.")

    def get_file_path(self, project_file: ProjectFile) -> str:
        parts = [cast(str, project_file.name)]
        parent_id = cast(int | None, project_file.parent_id)
        visited = {cast(int, project_file.id)}
        while parent_id is not None:
            if parent_id in visited:
                raise AgentToolError("Project file hierarchy is invalid.")
            visited.add(parent_id)
            parent = (
                self.db.query(ProjectFile)
                .filter(
                    ProjectFile.id == parent_id,
                    ProjectFile.project_id == self.project_id,
                    ProjectFile.type == "folder",
                )
                .first()
            )
            if parent is None:
                raise AgentToolError("Project file hierarchy is invalid.")
            parts.append(cast(str, parent.name))
            parent_id = cast(int | None, parent.parent_id)
        return "/".join(reversed(parts))

    @staticmethod
    def _validate_path(file_path: str) -> str:
        if (
            not file_path
            or "\x00" in file_path
            or "\\" in file_path
            or any(part in {"", ".", ".."} for part in file_path.split("/"))
        ):
            raise AgentToolError("Invalid project file path.")
        path = PurePosixPath(file_path)
        if path.is_absolute():
            raise AgentToolError("Invalid project file path.")
        return path.as_posix()

    @classmethod
    def is_safe_path(cls, path: str) -> bool:
        lowered_parts = [part.lower() for part in PurePosixPath(path).parts]
        return (
            CodeChunker.is_indexable_path(path)
            and not any(
                part.startswith(".env")
                or any(marker in part for marker in cls.SECRET_NAME_MARKERS)
                for part in lowered_parts
            )
        )

    @classmethod
    def contains_sensitive_content(cls, content: str | None) -> bool:
        return CodeChunker.contains_sensitive_content(content)