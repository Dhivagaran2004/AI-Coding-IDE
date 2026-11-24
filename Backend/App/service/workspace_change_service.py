import logging
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sqlalchemy.orm import Session
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from App.models.project import Project
from App.models.project_file import ProjectFile
from App.service.AI.agent.agent_tools import AgentTools
from App.service.AI.index.code_chunker import CodeChunker
from App.service.AI.index.repository_index_service import RepositoryIndexService
from App.service.AI.index.vector_rag_service import VectorRAGService
from App.service.project_workspace_service import (
    MAX_SANDBOX_PROJECT_FILES,
    ProjectWorkspaceService,
    ProjectWorkspaceSyncError,
)
from App.service.sandbox_service import SandboxService
from App.config import IDE_WORKSPACES_ROOT
from App.service.workspace_paths import (
    WorkspacePathError,
    ensure_project_workspace,
)

logger = logging.getLogger(__name__)
IGNORED_WORKSPACE_DIRECTORIES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
}
MAX_PENDING_WORKSPACE_EVENTS = 10_000
MAX_SYNCED_FILE_BYTES = 1_000_000


@dataclass(frozen=True)
class WorkspaceChange:
    project_id: int
    path: str
    operation: str
    is_directory: bool


class WorkspaceChangeManager:
    def __init__(self) -> None:
        self._events: queue.Queue[WorkspaceChange] = queue.Queue(
            maxsize=MAX_PENDING_WORKSPACE_EVENTS
        )
        self._observers: dict[int, tuple[Path, Observer]] = {}
        self._lock = threading.RLock()

    def watch(self, project_id: int, workspace: Path) -> None:
        root = workspace.resolve()
        with self._lock:
            current = self._observers.get(project_id)
            if current is not None and current[0] == root:
                return
            if current is not None:
                current[1].stop()
                current[1].join(timeout=5)

            manager = self

            class Handler(FileSystemEventHandler):
                def on_any_event(self, event: FileSystemEvent) -> None:
                    if event.event_type not in {"created", "modified", "deleted", "moved"}:
                        return
                    if event.is_directory and event.event_type == "modified":
                        return
                    if event.event_type == "moved":
                        manager._enqueue_path(
                            project_id,
                            root,
                            str(event.src_path),
                            "deleted",
                            event.is_directory,
                        )
                        manager._enqueue_path(
                            project_id,
                            root,
                            str(event.dest_path),
                            "created",
                            event.is_directory,
                        )
                    else:
                        manager._enqueue_path(
                            project_id,
                            root,
                            str(event.src_path),
                            event.event_type,
                            event.is_directory,
                        )

            root.mkdir(parents=True, exist_ok=True)
            observer = Observer()
            observer.schedule(Handler(), str(root), recursive=True)
            observer.start()
            self._observers[project_id] = (root, observer)

    def enqueue(
        self,
        project_id: int,
        path: str,
        operation: str,
        *,
        is_directory: bool = False,
    ) -> None:
        normalized = PurePosixPath(path).as_posix()
        if (
            "\\" in path
            or not normalized
            or PurePosixPath(normalized).is_absolute()
            or normalized == "."
            or normalized.startswith("../")
            or any(
                part.lower() in IGNORED_WORKSPACE_DIRECTORIES
                for part in PurePosixPath(normalized).parts
            )
        ):
            return
        try:
            self._events.put_nowait(
                WorkspaceChange(
                    project_id,
                    normalized,
                    operation,
                    is_directory,
                )
            )
        except queue.Full:
            logger.error(
                "Workspace event queue is full; changes for project %s may need rescan.",
                project_id,
            )

    def drain(self, project_id: int, limit: int = 2000) -> list[WorkspaceChange]:
        retained: list[WorkspaceChange] = []
        selected: dict[tuple[str, bool], WorkspaceChange] = {}
        for _ in range(limit):
            try:
                change = self._events.get_nowait()
            except queue.Empty:
                break
            if change.project_id == project_id:
                selected[(change.path, change.is_directory)] = change
            else:
                retained.append(change)
        for change in retained:
            self._events.put_nowait(change)
        return list(selected.values())

    def stop_all(self) -> None:
        with self._lock:
            observers = [observer for _, observer in self._observers.values()]
            self._observers.clear()
        for observer in observers:
            observer.stop()
        for observer in observers:
            observer.join(timeout=5)

    def is_watching(self, project_id: int) -> bool:
        with self._lock:
            return project_id in self._observers

    def _enqueue_path(
        self,
        project_id: int,
        root: Path,
        raw_path: str,
        operation: str,
        is_directory: bool,
    ) -> None:
        try:
            relative = Path(raw_path).resolve(strict=False).relative_to(root)
        except ValueError:
            logger.warning("Ignoring workspace event outside project %s.", project_id)
            return
        path = relative.as_posix()
        if not path or any(
            part.lower() in IGNORED_WORKSPACE_DIRECTORIES
            for part in PurePosixPath(path).parts
        ):
            return
        try:
            self._events.put_nowait(
                WorkspaceChange(project_id, path, operation, is_directory)
            )
        except queue.Full:
            logger.error(
                "Workspace event queue is full; changes for project %s may need rescan.",
                project_id,
            )


class WorkspaceChangeService:
    def __init__(
        self,
        db: Session,
        *,
        sandbox_service: SandboxService | None = None,
        change_manager: WorkspaceChangeManager,
    ) -> None:
        self.db = db
        self.sandbox = sandbox_service or SandboxService()
        self.change_manager = change_manager
        self._current_project_id: int | None = None
        self.workspace_service = ProjectWorkspaceService(
            db,
            sandbox_service=self.sandbox,
        )

    def sync_pending(self, project_id: int, user_id: int) -> dict[str, list[dict[str, object]]]:
        project = (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.user_id == user_id)
            .first()
        )
        if project is None:
            raise ProjectWorkspaceSyncError("Project not found.")
        self._current_project_id = project_id

        previous_path = project.workspace_path
        try:
            workspace = ensure_project_workspace(
                project,
                workspace_root=self.sandbox.workspace_root,
                legacy_roots=(
                    None
                    if self.sandbox.workspace_root == IDE_WORKSPACES_ROOT
                    else ()
                ),
            )
        except WorkspacePathError as error:
            raise ProjectWorkspaceSyncError(str(error)) from error
        if project.workspace_path != previous_path:
            self.db.commit()
        self.change_manager.watch(project_id, workspace)

        events = self.change_manager.drain(project_id)
        expanded: list[WorkspaceChange] = []
        for event in events:
            target = workspace.joinpath(*PurePosixPath(event.path).parts)
            if event.is_directory:
                if target.is_dir() and not target.is_symlink():
                    expanded.append(event)
                    discovered = 0
                    for directory, subdirectories, filenames in os.walk(
                        target,
                        topdown=True,
                        followlinks=False,
                    ):
                        parent = Path(directory)
                        subdirectories[:] = [
                            name
                            for name in subdirectories
                            if name.lower() not in IGNORED_WORKSPACE_DIRECTORIES
                            and not (parent / name).is_symlink()
                        ]
                        for filename in filenames:
                            path = parent / filename
                            if path.is_symlink():
                                continue
                            expanded.append(
                                WorkspaceChange(
                                    project_id,
                                    path.relative_to(workspace).as_posix(),
                                    "modified",
                                    False,
                                )
                            )
                            discovered += 1
                            if discovered > MAX_SANDBOX_PROJECT_FILES:
                                raise ProjectWorkspaceSyncError(
                                    "New workspace directory contains too many files to synchronize."
                                )
                else:
                    expanded.append(event)
            else:
                expanded.append(event)

        changes: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        updated_files: list[ProjectFile] = []
        deleted_file_ids: list[int] = []
        for event in expanded:
            path = event.path
            if not self._is_safe_relative_path(path):
                skipped.append({"path": path, "reason": "unsafe or protected path"})
                continue
            target = workspace.joinpath(*PurePosixPath(path).parts)
            self._ensure_no_symlinks(target, workspace)
            if event.is_directory:
                if target.exists():
                    self._ensure_folder(path)
                    changes.append({"path": path, "directory": True})
                else:
                    deleted_file_ids.extend(
                        self._delete_folder(path, changes)
                    )
                continue

            if target.is_symlink():
                skipped.append({"path": path, "reason": "symbolic links are not synchronized"})
                continue
            if target.is_file():
                result = self._sync_file_from_workspace(
                    path,
                    target,
                    changes,
                    skipped,
                    updated_files,
                )
                if result is not None:
                    continue
            else:
                deleted_file_ids.extend(self._delete_file(path, changes))

        if changes:
            self.db.commit()
        if deleted_file_ids:
            VectorRAGService(self.db, project_id).delete_files(deleted_file_ids)
        for project_file in updated_files:
            RepositoryIndexService(self.db, project_id).index_file_after_save(
                project_file,
                only_if_needed=True,
            )
        return {"changes": changes, "skipped": skipped}

    def _sync_file_from_workspace(
        self,
        path: str,
        target: Path,
        changes: list[dict[str, object]],
        skipped: list[dict[str, object]],
        updated_files: list[ProjectFile],
    ) -> bool | None:
        try:
            if target.stat().st_size > MAX_SYNCED_FILE_BYTES:
                skipped.append({"path": path, "reason": "file is too large or binary"})
                return True
            content_bytes = target.read_bytes()
        except FileNotFoundError:
            return None
        if b"\0" in content_bytes:
            skipped.append({"path": path, "reason": "file is too large or binary"})
            return True
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append({"path": path, "reason": "file is not UTF-8 text"})
            return True
        if not self._is_safe_workspace_file(path, content):
            skipped.append({"path": path, "reason": "file is excluded for safety"})
            return True

        parent_id = self._ensure_folder(PurePosixPath(path).parent.as_posix())
        name = PurePosixPath(path).name
        project_file = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == self._project_id,
                ProjectFile.parent_id == parent_id,
                ProjectFile.name == name,
            )
            .first()
        )
        if project_file is not None and project_file.type != "file":
            raise ProjectWorkspaceSyncError(
                f"A folder blocks workspace file path {path}."
            )
        if project_file is None:
            count = (
                self.db.query(ProjectFile)
                .filter(
                    ProjectFile.project_id == self._project_id,
                    ProjectFile.type == "file",
                )
                .count()
            )
            if count >= MAX_SANDBOX_PROJECT_FILES:
                skipped.append({"path": path, "reason": "project file limit reached"})
                return True
            project_file = ProjectFile(
                project_id=self._project_id,
                parent_id=parent_id,
                name=name,
                type="file",
                content=content,
                language=self.workspace_service._infer_language(name),
            )
            self.db.add(project_file)
            self.db.flush()
            changes.append({
                "path": path,
                "file_id": project_file.id,
                "content": content,
                "deleted": False,
            })
            updated_files.append(project_file)
        elif (project_file.content or "") != content:
            project_file.content = content
            project_file.language = self.workspace_service._infer_language(name)
            changes.append({
                "path": path,
                "file_id": project_file.id,
                "content": content,
                "deleted": False,
            })
            updated_files.append(project_file)
        return True

    @property
    def _project_id(self) -> int:
        if self._current_project_id is None:
            raise ProjectWorkspaceSyncError("Workspace project is not initialized.")
        return self._current_project_id

    def _ensure_folder(self, path: str) -> int | None:
        if path in {"", "."}:
            return None
        parent_id: int | None = None
        current = PurePosixPath()
        for part in PurePosixPath(path).parts:
            current /= part
            existing = (
                self.db.query(ProjectFile)
                .filter(
                    ProjectFile.project_id == self._project_id,
                    ProjectFile.parent_id == parent_id,
                    ProjectFile.name == part,
                )
                .first()
            )
            if existing is not None:
                if existing.type != "folder":
                    raise ProjectWorkspaceSyncError(
                        f"A project file blocks directory {current.as_posix()}."
                    )
                parent_id = existing.id
                continue
            folder = ProjectFile(
                project_id=self._project_id,
                parent_id=parent_id,
                name=part,
                type="folder",
                content=None,
                language=None,
            )
            self.db.add(folder)
            self.db.flush()
            parent_id = folder.id
        return parent_id

    def _delete_file(
        self,
        path: str,
        changes: list[dict[str, object]],
    ) -> list[int]:
        parts = PurePosixPath(path)
        parent_id = self._find_folder(parts.parent.as_posix())
        if parent_id is None and parts.parent.as_posix() != ".":
            return []
        project_file = (
            self.db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == self._project_id,
                ProjectFile.parent_id == parent_id,
                ProjectFile.name == parts.name,
                ProjectFile.type == "file",
            )
            .first()
        )
        if project_file is None:
            return []
        file_id = project_file.id
        self.db.delete(project_file)
        changes.append({
            "path": path,
            "file_id": file_id,
            "content": "",
            "deleted": True,
        })
        return [file_id]

    def _delete_folder(
        self,
        path: str,
        changes: list[dict[str, object]],
    ) -> list[int]:
        folder_id = self._find_folder(path)
        if folder_id is None:
            return []
        folder = self.db.get(ProjectFile, folder_id)
        if folder is None:
            return []
        deleted_file_ids: list[int] = []

        def delete_contents(parent: ProjectFile, prefix: str) -> None:
            children = (
                self.db.query(ProjectFile)
                .filter(
                    ProjectFile.project_id == self._project_id,
                    ProjectFile.parent_id == parent.id,
                )
                .all()
            )
            for child in children:
                child_path = f"{prefix}/{child.name}"
                if child.type == "folder":
                    delete_contents(child, child_path)
                else:
                    deleted_file_ids.append(child.id)
                    changes.append({
                        "path": child_path,
                        "file_id": child.id,
                        "content": "",
                        "deleted": True,
                    })
            self.db.delete(parent)

        delete_contents(folder, path)
        changes.append({"path": path, "directory": True, "deleted": True})
        return deleted_file_ids

    def _find_folder(self, path: str) -> int | None:
        if path in {"", "."}:
            return None
        parent_id: int | None = None
        for part in PurePosixPath(path).parts:
            folder = (
                self.db.query(ProjectFile)
                .filter(
                    ProjectFile.project_id == self._project_id,
                    ProjectFile.parent_id == parent_id,
                    ProjectFile.name == part,
                    ProjectFile.type == "folder",
                )
                .first()
            )
            if folder is None:
                return None
            parent_id = folder.id
        return parent_id

    @staticmethod
    def _is_safe_relative_path(path: str) -> bool:
        return (
            bool(path)
            and "\\" not in path
            and "\x00" not in path
            and not PurePosixPath(path).is_absolute()
            and all(part not in {"", ".", ".."} for part in path.split("/"))
        )

    @staticmethod
    def _is_safe_workspace_file(path: str, content: str) -> bool:
        lowered_parts = [part.lower() for part in PurePosixPath(path).parts]
        return (
            not any(
                part.startswith(".env")
                or any(marker in part for marker in AgentTools.SECRET_NAME_MARKERS)
                for part in lowered_parts
            )
            and not CodeChunker.contains_sensitive_content(content)
        )

    @staticmethod
    def _ensure_no_symlinks(target: Path, root: Path) -> None:
        try:
            target.resolve(strict=False).relative_to(root.resolve())
        except ValueError as error:
            raise ProjectWorkspaceSyncError(
                "Workspace change escaped the project directory."
            ) from error
        current = target
        while current != root:
            if current.is_symlink():
                raise ProjectWorkspaceSyncError(
                    "Workspace synchronization cannot follow symbolic links."
                )
            current = current.parent


workspace_change_manager = WorkspaceChangeManager()
