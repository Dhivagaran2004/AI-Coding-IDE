from pathlib import PurePosixPath, PureWindowsPath
from typing import Protocol, cast

from sqlalchemy.orm import Session

from App.models.project import Project
from App.models.project_file import ProjectFile
from App.models.repository_index import RepositoryIndex
from App.service.AI.agent.agent_tools import AgentTools
from App.service.AI.index.repository_index_service import (
    RepositoryIndexService,
)
from App.service.AI.index.vector_rag_service import VectorRAGService

MAX_RESULTING_FILE_CHARS = 1000000


class PatchNotFoundError(Exception):
    pass


class PatchValidationError(Exception):
    pass


class StalePatchError(Exception):
    pass


class UnsupportedPatchOperation(Exception):
    pass


class VectorIndexer(Protocol):
    def index_file(self, file: ProjectFile) -> int:
        ...


class PatchAction(Protocol):
    operation: str
    file_path: str
    start_line: int | None
    end_line: int | None
    old_code: str
    new_code: str


class CodeActionService:
    MAX_RESULTING_FILE_CHARS = MAX_RESULTING_FILE_CHARS

    def __init__(
        self,
        db: Session,
        vector_index_service: VectorIndexer | None = None,
    ):
        self.db = db
        self.vector_index_service = vector_index_service

    def apply_action(
        self,
        project_id: int,
        file_id: int | None,
        user_id: int,
        action: PatchAction,
    ) -> ProjectFile:
        project = (
            self.db.query(Project)
            .filter(
                Project.id == project_id,
                Project.user_id == user_id,
            )
            .first()
        )
        if project is None:
            raise PatchNotFoundError("Project not found.")

        if action.operation == "create_file":
            return self.create_file(project_id=project_id, user_id=user_id, action=action)

        if file_id is None:
            raise PatchNotFoundError("File not found in this project.")

        project_file = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.id == file_id,
                ProjectFile.project_id == project_id,
            )
            .first()
        )
        if project_file is None or cast(str, project_file.type) != "file":
            raise PatchNotFoundError("File not found in this project.")

        if action.operation != "replace":
            raise UnsupportedPatchOperation(
                "Only replace and create_file actions can currently be applied."
            )

        requested_path = self._validate_relative_path(action.file_path)
        current_path = self._get_file_path(project_id, project_file)
        if requested_path not in {current_path, project_file.name}:
            raise PatchValidationError(
                "Patch file_path does not match the selected project file."
            )

        lines = (
            cast(str | None, project_file.content) or ""
        ).splitlines(keepends=True)
        empty_file = not lines
        start_line = action.start_line
        end_line = action.end_line
        if (
            start_line is None
            or end_line is None
            or (
                empty_file
                and (start_line, end_line, action.old_code) != (1, 1, "")
            )
            or (not empty_file and end_line > len(lines))
        ):
            raise PatchValidationError("Patch line range is outside the file.")

        current_code = "" if empty_file else "".join(lines[start_line - 1:end_line])
        normalized_current = self._normalize_line_endings(current_code)
        normalized_expected = self._normalize_line_endings(action.old_code)
        if not self._matches_patch_text(normalized_current, normalized_expected):
            expected_lines = normalized_expected.splitlines(keepends=True)
            matches = [
                index
                for index in range(len(lines) - len(expected_lines) + 1)
                if expected_lines
                and self._matches_patch_text(
                    self._normalize_line_endings(
                        "".join(lines[index:index + len(expected_lines)])
                    ),
                    normalized_expected,
                )
            ]
            if len(matches) != 1:
                raise StalePatchError(
                    "The file changed since this patch was generated."
                )
            start_line = matches[0] + 1
            end_line = matches[0] + len(expected_lines)
            current_code = "".join(lines[matches[0]:matches[0] + len(expected_lines)])
            normalized_current = self._normalize_line_endings(current_code)

        normalized_replacement = self._normalize_line_endings(
            action.new_code
        )
        if self._matches_ignoring_indentation(
            normalized_current,
            normalized_expected,
        ):
            normalized_replacement = self._rebase_indentation(
                normalized_replacement,
                normalized_expected,
                normalized_current,
            )

        newline = "\r\n" if "\r\n" in (cast(str | None, project_file.content) or "") else "\n"
        replacement = normalized_replacement.replace(
            "\n",
            newline,
        )
        if current_code.endswith(("\n", "\r")) and not replacement.endswith(
            ("\n", "\r")
        ):
            replacement += newline
        elif not empty_file and not current_code.endswith(("\n", "\r")) and replacement.endswith(
            newline
        ):
            replacement = replacement[:-len(newline)]

        updated_content = (
            "".join(lines[:start_line - 1])
            + replacement
            + "".join(lines[end_line:])
        )
        if len(updated_content) > self.MAX_RESULTING_FILE_CHARS:
            raise PatchValidationError("Patched file exceeds the size limit.")

        setattr(project_file, "content", updated_content)
        self.db.commit()
        self.db.refresh(project_file)
        RepositoryIndexService(
            db=self.db,
            project_id=project_id,
            vector_index_service=self.vector_index_service,
        ).index_file_after_save(project_file)
        return project_file

    def delete_file(
        self,
        project_id: int,
        file_id: int,
        user_id: int,
        action: PatchAction,
    ) -> None:
        project = (
            self.db.query(Project)
            .filter(
                Project.id == project_id,
                Project.user_id == user_id,
            )
            .first()
        )
        if project is None:
            raise PatchNotFoundError("Project not found.")
        project_file = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.id == file_id,
                ProjectFile.project_id == project_id,
                ProjectFile.type == "file",
            )
            .with_for_update()
            .first()
        )
        if project_file is None:
            raise PatchNotFoundError("File not found in this project.")

        requested_path = self._validate_relative_path(action.file_path)
        if requested_path not in {
            self._get_file_path(project_id, project_file),
            project_file.name,
        }:
            raise PatchValidationError(
                "Delete file_path does not match the selected project file."
            )
        current_content = cast(str | None, project_file.content) or ""
        if action.operation != "delete" or not self._matches_patch_text(
            self._normalize_line_endings(current_content),
            self._normalize_line_endings(action.old_code),
        ):
            raise StalePatchError(
                "The file changed since this deletion was proposed."
            )

        self.db.query(RepositoryIndex).filter(
            RepositoryIndex.project_id == project_id,
            RepositoryIndex.file_id == file_id,
        ).delete(synchronize_session="fetch")
        VectorRAGService(
            db=self.db,
            project_id=project_id,
        ).delete_files([file_id])
        self.db.delete(project_file)
        self.db.commit()

    def create_file(
        self,
        project_id: int,
        user_id: int,
        action: PatchAction,
    ) -> ProjectFile:
        project = (
            self.db.query(Project)
            .filter(
                Project.id == project_id,
                Project.user_id == user_id,
            )
            .first()
        )
        if project is None:
            raise PatchNotFoundError("Project not found.")

        requested_path = self._validate_relative_path(action.file_path)
        if not AgentTools.is_safe_path(requested_path):
            raise PatchValidationError(
                "This file path is protected from AI file operations."
            )
        if self._find_file_by_path(project_id, requested_path) is not None:
            raise UnsupportedPatchOperation(
                "Only replace actions can currently be applied."
            )

        file_name = PurePosixPath(requested_path).name
        parent_id = None
        relative_parent = PurePosixPath(requested_path).parent
        if relative_parent != PurePosixPath("."):
            for parent_part in relative_parent.parts:
                if parent_part in {"", ".", ".."}:
                    continue
                parent = (
                    self.db.query(ProjectFile)
                    .filter(
                        ProjectFile.project_id == project_id,
                        ProjectFile.parent_id == parent_id,
                        ProjectFile.name == parent_part,
                    )
                    .first()
                )
                if parent is not None and cast(str, parent.type) != "folder":
                    raise PatchValidationError(
                        f"Cannot create a file beneath non-folder path {parent_part}."
                    )
                if parent is None:
                    parent = ProjectFile(
                        project_id=project_id,
                        parent_id=parent_id,
                        name=parent_part,
                        type="folder",
                        content=None,
                        language=None,
                    )
                    self.db.add(parent)
                    self.db.flush()
                parent_id = cast(int, parent.id)

        existing_file = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == project_id,
                ProjectFile.parent_id == parent_id,
                ProjectFile.name == file_name,
            )
            .first()
        )
        if existing_file is not None:
            raise PatchValidationError("A file already exists at this path.")

        project_file = ProjectFile(
            project_id=project_id,
            parent_id=parent_id,
            name=file_name,
            type="file",
            content=action.new_code,
            language=self._infer_language(requested_path),
        )
        self.db.add(project_file)
        self.db.commit()
        self.db.refresh(project_file)
        RepositoryIndexService(
            db=self.db,
            project_id=project_id,
        ).index_file_after_save(project_file)
        return project_file

    @staticmethod
    def _normalize_line_endings(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")

    def _find_file_by_path(self, project_id: int, file_path: str) -> ProjectFile | None:
        for project_file in (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == project_id,
                ProjectFile.type == "file",
            )
            .all()
        ):
            if self._get_file_path(project_id, project_file) == file_path:
                return project_file
        return None

    @staticmethod
    def _infer_language(file_path: str) -> str | None:
        lower_name = file_path.lower()
        extension_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".java": "java",
            ".c": "c",
            ".h": "c",
            ".cpp": "cpp",
            ".cc": "cpp",
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
        for extension, language in extension_map.items():
            if lower_name.endswith(extension):
                return language
        return None

    @classmethod
    def _matches_patch_text(cls, current: str, expected: str) -> bool:
        return (
            current == expected
            or current.removesuffix("\n") == expected.removesuffix("\n")
            or cls._matches_ignoring_indentation(current, expected)
        )

    @staticmethod
    def _matches_ignoring_indentation(
        current: str,
        expected: str,
    ) -> bool:
        current_lines = current.removesuffix("\n").splitlines()
        expected_lines = expected.removesuffix("\n").splitlines()
        return (
            len(current_lines) == len(expected_lines)
            and all(
                actual.lstrip(" \t") == wanted.lstrip(" \t")
                for actual, wanted in zip(current_lines, expected_lines)
            )
        )

    @staticmethod
    def _rebase_indentation(
        replacement: str,
        expected: str,
        current: str,
    ) -> str:
        def first_indent(value: str) -> str:
            for line in value.splitlines():
                if line.strip():
                    return line[:len(line) - len(line.lstrip(" \t"))]
            return ""

        expected_indent = first_indent(expected)
        current_indent = first_indent(current)
        replacement_indent = first_indent(replacement)
        if replacement_indent != expected_indent:
            return replacement

        lines = replacement.splitlines(keepends=True)
        rebased: list[str] = []
        for line in lines:
            body = line.rstrip("\r\n")
            ending = line[len(body):]
            if body.strip():
                body = current_indent + body[len(expected_indent):]
            rebased.append(body + ending)
        return "".join(rebased)

    @staticmethod
    def _validate_relative_path(file_path: str) -> str:
        if (
            "\x00" in file_path
            or "\\" in file_path
            or PureWindowsPath(file_path).drive
            or any(part in {"", ".", ".."} for part in file_path.split("/"))
        ):
            raise PatchValidationError("Invalid patch file path.")
        path = PurePosixPath(file_path)
        if path.is_absolute() or any(
            part in {"", ".", ".."} for part in path.parts
        ):
            raise PatchValidationError("Invalid patch file path.")
        return path.as_posix()

    def _get_file_path(
        self,
        project_id: int,
        project_file: ProjectFile,
    ) -> str:
        parts = [cast(str, project_file.name)]
        parent_id = cast(int | None, project_file.parent_id)
        visited = {cast(int, project_file.id)}
        while parent_id is not None and parent_id not in visited:
            visited.add(parent_id)
            parent = (
                self.db.query(ProjectFile)
                .filter(
                    ProjectFile.id == parent_id,
                    ProjectFile.project_id == project_id,
                )
                .first()
            )
            if parent is None or cast(str, parent.type) != "folder":
                raise PatchValidationError(
                    "Patch file has an invalid project path."
                )
            parts.append(cast(str, parent.name))
            parent_id = cast(int | None, parent.parent_id)
        return "/".join(reversed(parts))