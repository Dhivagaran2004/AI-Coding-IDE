from pathlib import Path
import stat

import pytest

from App.models.project import Project
from App.service.workspace_paths import (
    WorkspacePathError,
    ensure_project_workspace,
)


def _project(project_id: int = 1) -> Project:
    return Project(
        id=project_id,
        user_id=1,
        name=f"Project {project_id}",
        language="python",
    )


def test_legacy_workspace_is_moved_without_losing_files(tmp_path):
    root = tmp_path / "external"
    legacy_root = tmp_path / "backend" / "workspaces"
    legacy_project = legacy_root / "1"
    legacy_project.mkdir(parents=True)
    (legacy_project / "main.py").write_text("print('kept')\n", encoding="utf-8")

    project = _project()
    workspace = ensure_project_workspace(
        project,
        workspace_root=root,
        legacy_roots=(legacy_root,),
    )

    assert workspace == root.resolve() / "1"
    assert (workspace / "main.py").read_text(encoding="utf-8") == "print('kept')\n"
    assert not legacy_project.exists()
    assert project.workspace_path == str(workspace)


def test_legacy_workspace_conflicts_are_not_overwritten(tmp_path):
    root = tmp_path / "external"
    legacy_root = tmp_path / "backend" / "workspaces"
    legacy_project = legacy_root / "1"
    destination = root / "1"
    legacy_project.mkdir(parents=True)
    destination.mkdir(parents=True)
    (legacy_project / "main.py").write_text("legacy\n", encoding="utf-8")
    (destination / "main.py").write_text("external\n", encoding="utf-8")

    with pytest.raises(WorkspacePathError, match="conflicting files"):
        ensure_project_workspace(
            _project(),
            workspace_root=root,
            legacy_roots=(legacy_root,),
        )

    assert (legacy_project / "main.py").read_text(encoding="utf-8") == "legacy\n"
    assert (destination / "main.py").read_text(encoding="utf-8") == "external\n"


def test_legacy_workspace_merge_removes_read_only_source(tmp_path):
    root = tmp_path / "external"
    legacy_root = tmp_path / "backend" / "workspaces"
    legacy_project = legacy_root / "1"
    destination = root / "1"
    legacy_project.mkdir(parents=True)
    destination.mkdir(parents=True)
    legacy_file = legacy_project / "main.py"
    legacy_file.write_text("print('kept')\n", encoding="utf-8")
    (destination / "main.py").write_text("print('kept')\n", encoding="utf-8")
    legacy_file.chmod(stat.S_IREAD)
    legacy_project.chmod(stat.S_IREAD)

    workspace = ensure_project_workspace(
        _project(),
        workspace_root=root,
        legacy_roots=(legacy_root,),
    )

    assert workspace == destination.resolve()
    assert (workspace / "main.py").read_text(encoding="utf-8") == "print('kept')\n"
    assert not legacy_project.exists()


def test_missing_workspace_is_created_under_root(tmp_path):
    root = tmp_path / "external"

    workspace = ensure_project_workspace(
        _project(4),
        workspace_root=root,
        legacy_roots=(),
    )

    assert workspace == root.resolve() / "4"
    assert workspace.is_dir()


def test_workspace_root_inside_ide_source_is_rejected():
    from App.config import IDE_SOURCE_ROOT

    with pytest.raises(WorkspacePathError, match="outside the IDE source"):
        ensure_project_workspace(
            _project(),
            workspace_root=Path(IDE_SOURCE_ROOT) / "unsafe-workspaces",
            legacy_roots=(),
        )
