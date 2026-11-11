import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import cast

from sqlalchemy.orm import Session

from App.models.project import Project
from App.models.project_file import ProjectFile
from App.service.AI.agent.agent_tools import AgentToolError, AgentTools
from App.service.AI.index.code_chunker import CodeChunker
from App.service.AI.index.repository_index_service import RepositoryIndexService
from App.service.AI.index.vector_rag_service import VectorRAGService
from App.service.sandbox_service import SandboxExecutionResult, SandboxService


MAX_SANDBOX_PROJECT_FILES = 1000
MAX_SANDBOX_PROJECT_BYTES = 50_000_000


class ProjectWorkspaceSyncError(RuntimeError):
    pass


class ProjectWorkspaceService:
    """Run sandbox commands against the database-backed project files."""

    def __init__(
        self,
        db: Session,
        sandbox_service: SandboxService | None = None,
    ):
        self.db = db
        self.sandbox = sandbox_service or SandboxService()

    def execute_command(
        self,
        project_id: int,
        user_id: int,
        command: str,
        timeout: int | None = None,
    ) -> tuple[SandboxExecutionResult, list[dict[str, object]]]:
        project = (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )
        if project is None:
            raise ProjectWorkspaceSyncError("Project not found.")

        files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == project_id,
                ProjectFile.type == "file",
            )
            .all()
        )
        snapshot: dict[str, str] = {}
        total_bytes = 0
        for project_file in files:
            path = self._get_project_path(project_file)
            content = cast(str | None, project_file.content) or ""
            if not self._is_safe_project_file(path, content):
                continue
            total_bytes += len(content.encode("utf-8"))
            if len(snapshot) >= MAX_SANDBOX_PROJECT_FILES:
                raise ProjectWorkspaceSyncError(
                    "Project exceeds the sandbox file-count limit."
                )
            if total_bytes > MAX_SANDBOX_PROJECT_BYTES:
                raise ProjectWorkspaceSyncError(
                    "Project exceeds the sandbox size limit."
                )
            snapshot[path] = content

        self._sync_workspace_baseline(project_id, snapshot)
        changes: list[dict[str, object]] = []

        def persist_snapshot_changes(
            workspace: Path,
            result: SandboxExecutionResult,
        ) -> None:
            changes.extend(
                self._persist_changes(
                    project_id=project_id,
                    initial_files=snapshot,
                    workspace=workspace,
                    result=result,
                )
            )

        result = self.sandbox.execute_snapshot(
            project_id=project_id,
            files=snapshot,
            command=command,
            timeout=timeout,
            on_success=persist_snapshot_changes,
        )
        return result, changes

    def sync_file(
        self,
        project_id: int,
        user_id: int,
        file_path: str,
        content: str | None,
    ) -> None:
        project = (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )
        if project is None:
            raise ProjectWorkspaceSyncError("Project not found.")
        relative_path = self._validate_relative_path(file_path)
        if relative_path != file_path or not AgentTools.is_safe_path(relative_path):
            raise ProjectWorkspaceSyncError("Invalid or protected project file path.")
        if content is not None and (
            len(content.encode("utf-8")) > 1_000_000 or "\x00" in content
        ):
            raise ProjectWorkspaceSyncError("Project file cannot be safely synchronized.")

        project_directory = self.sandbox.get_project_directory(project_id)
        target = project_directory.joinpath(*PurePosixPath(relative_path).parts)
        self._ensure_contained(target, project_directory)
        for parent in target.parents:
            if parent == project_directory.parent:
                break
            if parent.is_symlink():
                raise ProjectWorkspaceSyncError(
                    "Workspace synchronization cannot follow symbolic links."
                )
        if target.is_symlink():
            raise ProjectWorkspaceSyncError(
                "Workspace synchronization cannot follow symbolic links."
            )
        if content is None:
            if target.exists() and not target.is_file():
                raise ProjectWorkspaceSyncError(
                    "Workspace deletion target is not a regular file."
                )
            if target.exists():
                target.unlink()
            return
        if target.exists() and not target.is_file():
            raise ProjectWorkspaceSyncError(
                "Workspace synchronization target is not a regular file."
            )
        self._write_workspace_file(target, content.encode("utf-8"))

    def _persist_changes(
        self,
        project_id: int,
        initial_files: dict[str, str],
        workspace: Path,
        result: SandboxExecutionResult,
    ) -> list[dict[str, object]]:
        changed_paths = (
            set(result.created_files)
            | set(result.modified_files)
            | set(result.deleted_files)
        )
        if not changed_paths:
            return []

        current_files = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == project_id,
                ProjectFile.type == "file",
            )
            .with_for_update()
            .all()
        )
        current_by_path = {
            self._get_project_path(project_file): project_file
            for project_file in current_files
        }
        folder_by_path = {
            self._get_project_path(project_file): project_file
            for project_file in self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == project_id,
                ProjectFile.type == "folder",
            )
            .all()
        }

        updated: list[ProjectFile] = []
        deleted_ids: list[int] = []
        response_changes: list[dict[str, object]] = []
        workspace_sync_started = False
        self._validate_workspace_targets(
            project_id=project_id,
            changed_paths=changed_paths,
            initial_files=initial_files,
            workspace=workspace,
            deleted_paths=set(result.deleted_files),
        )
        try:
            for relative_path in sorted(changed_paths):
                normalized_path = self._validate_relative_path(relative_path)
                if normalized_path != relative_path:
                    raise ProjectWorkspaceSyncError(
                        "Sandbox returned an invalid changed-file path."
                    )
                target = workspace.joinpath(*PurePosixPath(relative_path).parts)
                try:
                    target.resolve().relative_to(workspace.resolve())
                except ValueError as error:
                    raise ProjectWorkspaceSyncError(
                        "Sandbox change escaped the project workspace."
                    ) from error
                if target.is_symlink():
                    raise ProjectWorkspaceSyncError(
                        "Sandbox changes cannot contain symbolic links."
                    )

                existing = current_by_path.get(relative_path)
                initial = initial_files.get(relative_path)
                if relative_path in result.deleted_files:
                    if existing is None or initial is None:
                        raise ProjectWorkspaceSyncError(
                            f"Cannot apply stale sandbox deletion for {relative_path}."
                        )
                    if (existing.content or "") != initial:
                        raise ProjectWorkspaceSyncError(
                            f"{relative_path} changed while sandbox validation was running."
                        )
                    file_id = cast(int, existing.id)
                    deleted_ids.append(file_id)
                    self.db.delete(existing)
                    response_changes.append({
                        "path": relative_path,
                        "content": "",
                        "file_id": file_id,
                        "deleted": True,
                    })
                    continue

                content_bytes = target.read_bytes()
                if len(content_bytes) > 1_000_000 or b"\0" in content_bytes:
                    raise ProjectWorkspaceSyncError(
                        f"Sandbox output {relative_path} is too large or binary."
                    )
                content = content_bytes.decode("utf-8")
                if not self._is_safe_project_file(relative_path, content):
                    raise ProjectWorkspaceSyncError(
                        f"Sandbox output {relative_path} is excluded for safety."
                    )

                if relative_path in result.modified_files:
                    if existing is None or initial is None:
                        raise ProjectWorkspaceSyncError(
                            f"Cannot apply stale sandbox update for {relative_path}."
                        )
                    if (existing.content or "") != initial:
                        raise ProjectWorkspaceSyncError(
                            f"{relative_path} changed while sandbox validation was running."
                        )
                    existing.content = content
                    updated.append(existing)
                    response_changes.append({
                        "path": relative_path,
                        "content": content,
                        "file_id": cast(int, existing.id),
                        "deleted": False,
                    })
                    continue

                if existing is not None or relative_path in initial_files:
                    raise ProjectWorkspaceSyncError(
                        f"A project file already exists at {relative_path}."
                    )
                parent_path = PurePosixPath(relative_path).parent.as_posix()
                while parent_path != ".":
                    if parent_path in current_by_path:
                        raise ProjectWorkspaceSyncError(
                            f"A project file blocks the directory {parent_path}."
                        )
                    parent_path = PurePosixPath(parent_path).parent.as_posix()
                project_file = self._create_project_file(
                    project_id=project_id,
                    file_path=relative_path,
                    content=content,
                    folder_by_path=folder_by_path,
                )
                self.db.add(project_file)
                self.db.flush()
                updated.append(project_file)
                current_by_path[relative_path] = project_file
                response_changes.append({
                    "path": relative_path,
                    "content": content,
                    "file_id": cast(int, project_file.id),
                    "deleted": False,
                })

            workspace_sync_started = True
            self._sync_workspace_directory(
                project_id=project_id,
                workspace=workspace,
                changed_paths=changed_paths,
                deleted_paths=set(result.deleted_files),
            )
            self.db.commit()
        except Exception as error:
            self.db.rollback()
            if workspace_sync_started:
                try:
                    self._restore_workspace_directory(
                        project_id=project_id,
                        initial_files=initial_files,
                        changed_paths=changed_paths,
                    )
                except Exception as restore_error:
                    raise ProjectWorkspaceSyncError(
                        "Database synchronization failed and the workspace could "
                        "not be restored."
                    ) from restore_error
            raise

        if deleted_ids:
            VectorRAGService(self.db, project_id).delete_files(deleted_ids)
        for project_file in updated:
            RepositoryIndexService(self.db, project_id).index_file_after_save(
                project_file
            )
        return response_changes

    def _create_project_file(
        self,
        project_id: int,
        file_path: str,
        content: str,
        folder_by_path: dict[str, ProjectFile],
    ) -> ProjectFile:
        path = PurePosixPath(file_path)
        parent_id: int | None = None
        current_parent = PurePosixPath()
        for part in path.parts[:-1]:
            current_parent /= part
            parent_path = current_parent.as_posix()
            folder = folder_by_path.get(parent_path)
            if folder is None:
                folder = ProjectFile(
                    project_id=project_id,
                    parent_id=parent_id,
                    name=part,
                    type="folder",
                    content=None,
                    language=None,
                )
                self.db.add(folder)
                self.db.flush()
                folder_by_path[parent_path] = folder
            parent_id = cast(int, folder.id)
        return ProjectFile(
            project_id=project_id,
            parent_id=parent_id,
            name=path.name,
            type="file",
            content=content,
            language=self._infer_language(path.name),
        )

    def _sync_workspace_directory(
        self,
        project_id: int,
        workspace: Path,
        changed_paths: set[str],
        deleted_paths: set[str],
    ) -> None:
        project_directory = self.sandbox.get_project_directory(project_id)
        for relative_path in sorted(changed_paths - deleted_paths):
            source = workspace.joinpath(*PurePosixPath(relative_path).parts)
            destination = project_directory.joinpath(
                *PurePosixPath(relative_path).parts
            )
            self._ensure_contained(destination, project_directory)
            if source.is_symlink() or destination.is_symlink():
                raise ProjectWorkspaceSyncError(
                    "Workspace synchronization cannot follow symbolic links."
                )
            self._write_workspace_file(destination, source.read_bytes())

        for relative_path in deleted_paths:
            target = project_directory.joinpath(*PurePosixPath(relative_path).parts)
            self._ensure_contained(target, project_directory)
            if target.is_symlink():
                target.unlink()
            elif target.is_file():
                target.unlink()

    def _sync_workspace_baseline(
        self,
        project_id: int,
        files: dict[str, str],
    ) -> None:
        project_directory = self.sandbox.get_project_directory(project_id)
        for relative_path, content in files.items():
            target = project_directory.joinpath(
                *PurePosixPath(relative_path).parts
            )
            self._ensure_contained(target, project_directory)
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise ProjectWorkspaceSyncError(
                    f"Workspace path {relative_path} is not a regular file."
                )
            self._write_workspace_file(target, content.encode("utf-8"))

    @staticmethod
    def _write_workspace_file(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        try:
            os.replace(temporary_path, target)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _validate_workspace_targets(
        self,
        project_id: int,
        changed_paths: set[str],
        initial_files: dict[str, str],
        workspace: Path,
        deleted_paths: set[str],
    ) -> None:
        project_directory = self.sandbox.get_project_directory(project_id)
        for relative_path in changed_paths:
            target = project_directory.joinpath(*PurePosixPath(relative_path).parts)
            self._ensure_contained(target, project_directory)
            if not target.exists() and not target.is_symlink():
                continue
            if target.is_symlink() or not target.is_file():
                raise ProjectWorkspaceSyncError(
                    f"Workspace path {relative_path} is not a regular file."
                )
            current_bytes = target.read_bytes()
            if b"\0" in current_bytes or len(current_bytes) > 1_000_000:
                raise ProjectWorkspaceSyncError(
                    f"Workspace file {relative_path} cannot be safely synchronized."
                )
            current = current_bytes.decode("utf-8")
            baseline = initial_files.get(relative_path)
            sandbox_target = workspace.joinpath(
                *PurePosixPath(relative_path).parts
            )
            sandbox_content = (
                sandbox_target.read_text(encoding="utf-8")
                if relative_path not in deleted_paths
                else None
            )
            if baseline is None:
                if current != sandbox_content:
                    raise ProjectWorkspaceSyncError(
                        f"Workspace file {relative_path} conflicts with a sandbox-created file."
                    )
            elif relative_path in deleted_paths:
                if current != baseline:
                    raise ProjectWorkspaceSyncError(
                        f"Workspace file {relative_path} changed independently of the project records."
                    )
            elif current != baseline and current != sandbox_content:
                raise ProjectWorkspaceSyncError(
                    f"Workspace file {relative_path} changed independently of the project records."
                )

    def _restore_workspace_directory(
        self,
        project_id: int,
        initial_files: dict[str, str],
        changed_paths: set[str],
    ) -> None:
        project_directory = self.sandbox.get_project_directory(project_id)
        for relative_path in sorted(changed_paths):
            target = project_directory.joinpath(
                *PurePosixPath(relative_path).parts
            )
            self._ensure_contained(target, project_directory)
            if target.is_symlink():
                raise ProjectWorkspaceSyncError(
                    "Workspace rollback cannot follow symbolic links."
                )
            original_content = initial_files.get(relative_path)
            if original_content is None:
                if target.is_file():
                    target.unlink()
                elif target.exists():
                    raise ProjectWorkspaceSyncError(
                        f"Workspace rollback target {relative_path} is not a file."
                    )
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                delete=False,
            ) as temporary:
                temporary.write(original_content)
                temporary_path = Path(temporary.name)
            try:
                os.replace(temporary_path, target)
            finally:
                if temporary_path.exists():
                    temporary_path.unlink()

    def _get_project_path(self, project_file: ProjectFile) -> str:
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
                    ProjectFile.project_id == cast(int, project_file.project_id),
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
    def _is_safe_project_file(file_path: str, content: str) -> bool:
        return (
            AgentTools.is_safe_path(file_path)
            and not CodeChunker.contains_sensitive_content(content)
        )

    @staticmethod
    def _validate_relative_path(file_path: str) -> str:
        if (
            not file_path
            or "\x00" in file_path
            or "\\" in file_path
            or any(part in {"", ".", ".."} for part in file_path.split("/"))
        ):
            raise ProjectWorkspaceSyncError("Invalid project file path.")
        path = PurePosixPath(file_path)
        if path.is_absolute():
            raise ProjectWorkspaceSyncError("Invalid project file path.")
        return path.as_posix()

    @staticmethod
    def _ensure_contained(target: Path, root: Path) -> None:
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError as error:
            raise ProjectWorkspaceSyncError(
                "Workspace path escaped the project directory."
            ) from error

    @staticmethod
    def _infer_language(file_name: str) -> str | None:
        return {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".java": "java",
            ".html": "html",
            ".css": "css",
            ".json": "json",
            ".md": "markdown",
        }.get(Path(file_name).suffix.lower())
