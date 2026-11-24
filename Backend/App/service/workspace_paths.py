import errno
import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

from App.config import IDE_SOURCE_ROOT, IDE_WORKSPACES_ROOT, ROOT_DIR
from App.models.project import Project


class WorkspacePathError(RuntimeError):
    pass


def _remove_readonly_path(
    function: Callable[[str], object],
    path: str,
    error_info: tuple[object, BaseException, object],
) -> None:
    error = error_info[1]
    if not isinstance(error, PermissionError):
        raise error
    mode = stat.S_IREAD | stat.S_IWRITE
    if os.path.isdir(path):
        mode |= stat.S_IEXEC
    os.chmod(path, mode)
    function(path)


def _remove_legacy_workspace(path: Path) -> None:
    shutil.rmtree(path, onerror=_remove_readonly_path)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _validate_workspace_root(root: Path) -> Path:
    resolved_root = root.expanduser().resolve()
    if _is_within(resolved_root, IDE_SOURCE_ROOT):
        raise WorkspacePathError(
            "Project workspaces must be outside the IDE source directory."
        )
    return resolved_root


def _tree_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise WorkspacePathError(
                "Workspace migration cannot follow symbolic links."
            )
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hashes[relative] = digest
    return hashes


def _merge_workspace(source: Path, destination: Path) -> None:
    source_hashes = _tree_hashes(source)
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise WorkspacePathError(
                "The external project workspace is not a regular directory."
            )
        destination_hashes = _tree_hashes(destination)
        conflicts = [
            path
            for path, digest in source_hashes.items()
            if path in destination_hashes and destination_hashes[path] != digest
        ]
        if conflicts:
            raise WorkspacePathError(
                "Workspace migration found conflicting files; neither copy was "
                "overwritten."
            )

    destination.mkdir(parents=True, exist_ok=True)
    for source_path in source.rglob("*"):
        relative = source_path.relative_to(source)
        destination_path = destination / relative
        if source_path.is_dir():
            destination_path.mkdir(parents=True, exist_ok=True)
            continue
        digest = source_hashes[relative.as_posix()]
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if destination_path.exists():
            if hashlib.sha256(destination_path.read_bytes()).hexdigest() != digest:
                raise WorkspacePathError(
                    "Workspace migration found a conflicting file; neither copy "
                    "was overwritten."
                )
            continue
        shutil.copy2(source_path, destination_path)

    destination_hashes = _tree_hashes(destination)
    if any(
        destination_hashes.get(path) != digest
        for path, digest in source_hashes.items()
    ):
        raise WorkspacePathError("Workspace migration verification failed.")

    _remove_legacy_workspace(source)


def _legacy_workspace_roots() -> tuple[Path, ...]:
    return (
        ROOT_DIR / "workspaces",
        IDE_SOURCE_ROOT / "workspaces",
    )


def ensure_project_workspace(
    project: Project,
    *,
    workspace_root: Path | None = None,
    legacy_roots: tuple[Path, ...] | None = None,
) -> Path:
    project_id = project.id
    if project_id is None or project_id <= 0:
        raise WorkspacePathError("A persisted project is required.")

    root = _validate_workspace_root(
        workspace_root or IDE_WORKSPACES_ROOT
    )
    root.mkdir(parents=True, exist_ok=True)
    destination = (root / str(project_id)).resolve()
    if not _is_within(destination, root):
        raise WorkspacePathError("Project workspace escaped its configured root.")
    if destination.is_symlink():
        raise WorkspacePathError("Project workspace cannot be a symbolic link.")

    allowed_legacy_roots = legacy_roots
    if allowed_legacy_roots is None:
        allowed_legacy_roots = _legacy_workspace_roots()

    source: Path | None = None
    recorded_path = getattr(project, "workspace_path", None)
    if recorded_path:
        recorded = Path(recorded_path).expanduser().resolve()
        if recorded == destination:
            source = destination if destination.exists() else None
        else:
            allowed_paths = {
                (legacy_root / str(project_id)).resolve()
                for legacy_root in allowed_legacy_roots
            }
            if recorded not in allowed_paths:
                raise WorkspacePathError(
                    "Stored project workspace is outside the configured and "
                    "legacy workspace roots."
                )
            source = recorded if recorded.exists() else None

    if source is None and not recorded_path:
        source = next(
            (
                candidate.resolve()
                for legacy_root in allowed_legacy_roots
                for candidate in (legacy_root / str(project_id),)
                if candidate.exists() and candidate.resolve() != destination
            ),
            None,
        )

    if source is not None and source != destination:
        if source.is_symlink() or not source.is_dir():
            raise WorkspacePathError(
                "Legacy project workspace is not a regular directory."
            )
        if not any(
            _is_within(source, legacy.resolve())
            for legacy in allowed_legacy_roots
        ) and not _is_within(source, root):
            raise WorkspacePathError("Stored workspace path is not permitted.")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                os.replace(source, destination)
            else:
                _merge_workspace(source, destination)
        except OSError as error:
            if (
                error.errno != errno.EXDEV
                and getattr(error, "winerror", None) != 17
            ) or destination.exists():
                raise WorkspacePathError(
                    "Unable to migrate the project workspace safely."
                ) from error
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{project_id}-migration-",
                    dir=root,
                )
            )
            try:
                shutil.copytree(source, staging, dirs_exist_ok=True)
                if _tree_hashes(staging) != _tree_hashes(source):
                    raise WorkspacePathError(
                        "Workspace migration verification failed."
                    )
                os.replace(staging, destination)
                _remove_legacy_workspace(source)
            except Exception:
                if staging.exists():
                    shutil.rmtree(staging)
                raise
    elif not destination.exists():
        destination.mkdir(parents=True, exist_ok=True)

    if destination.is_symlink() or not destination.is_dir():
        raise WorkspacePathError("Project workspace is not a regular directory.")
    if not _is_within(destination, root):
        raise WorkspacePathError("Project workspace escaped its configured root.")

    project.workspace_path = str(destination)
    return destination
