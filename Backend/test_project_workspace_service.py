from datetime import datetime
import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from App.database.database import Base
from App.models.project import Project
from App.models.project_file import ProjectFile
from App.models.user import User
from App.service import project_workspace_service
from App.service.project_workspace_service import (
    ProjectWorkspaceService,
    ProjectWorkspaceSyncError,
)
from App.service.sandbox_service import SandboxService


@pytest.fixture
def workspace_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        User(
            id=1,
            name="Owner",
            email="workspace-owner@example.test",
            password_hash="x",
            created_at=datetime.now(),
        ),
        User(
            id=2,
            name="Other",
            email="workspace-other@example.test",
            password_hash="x",
            created_at=datetime.now(),
        ),
    ])
    session.flush()
    session.add_all([
        Project(id=1, user_id=1, name="Owned", language="python"),
        Project(id=2, user_id=2, name="Private", language="python"),
    ])
    session.flush()
    session.add_all([
        ProjectFile(
            id=11,
            project_id=1,
            name="main.py",
            type="file",
            content="print('before')\n",
            language="python",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
        ProjectFile(
            id=12,
            project_id=1,
            name=".env",
            type="file",
            content="API_KEY=private-value\n",
            language="dotenv",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
        ProjectFile(
            id=13,
            project_id=1,
            name="src",
            type="folder",
            content=None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
        ProjectFile(
            id=14,
            project_id=1,
            parent_id=13,
            name="old.py",
            type="file",
            content="print('old')\n",
            language="python",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
    ])
    session.commit()
    yield session
    session.close()
    engine.dispose()


def make_sandbox(tmp_path):
    workspace_root = tmp_path / "workspaces"
    project_directory = workspace_root / "1"
    project_directory.mkdir(parents=True)
    (project_directory / "main.py").write_bytes(b"print('before')\n")
    (project_directory / "src").mkdir()
    (project_directory / "src" / "old.py").write_bytes(b"print('old')\n")
    return SandboxService(
        workspace_root=str(workspace_root),
        sandbox_root=str(tmp_path / "temporary" / "sandbox"),
    )


def disable_index_side_effects(monkeypatch):
    class NoopRepositoryIndexer:
        def __init__(self, *_args, **_kwargs):
            pass

        def index_file_after_save(self, _file):
            pass

    class NoopVectorService:
        def __init__(self, *_args, **_kwargs):
            pass

        def delete_files(self, _file_ids):
            pass

    monkeypatch.setattr(
        project_workspace_service,
        "RepositoryIndexService",
        NoopRepositoryIndexer,
    )
    monkeypatch.setattr(
        project_workspace_service,
        "VectorRAGService",
        NoopVectorService,
    )


def test_snapshot_execution_persists_changes_to_database_and_workspace(
    workspace_db,
    tmp_path,
    monkeypatch,
):
    disable_index_side_effects(monkeypatch)
    sandbox = make_sandbox(tmp_path)

    def run_container(_name, sandbox_workspace, _command, _timeout):
        assert (sandbox_workspace / "main.py").is_file()
        assert not (sandbox_workspace / ".env").exists()
        (sandbox_workspace / "main.py").write_bytes(b"print('after')\n")
        (sandbox_workspace / "src" / "new.py").write_bytes(b"print('new')\n")
        (sandbox_workspace / "src" / "old.py").unlink()
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="finished",
            stderr="",
        )

    monkeypatch.setattr(sandbox, "_run_container", run_container)
    result, changes = ProjectWorkspaceService(
        workspace_db,
        sandbox_service=sandbox,
    ).execute_command(
        project_id=1,
        user_id=1,
        command="python main.py",
    )

    assert result.success is True
    assert result.modified_files == ["main.py"]
    assert result.created_files == ["src/new.py"]
    assert result.deleted_files == ["src/old.py"]
    assert {change["path"]: change["deleted"] for change in changes} == {
        "main.py": False,
        "src/new.py": False,
        "src/old.py": True,
    }
    assert workspace_db.get(ProjectFile, 11).content == "print('after')\n"
    assert workspace_db.get(ProjectFile, 14) is None
    new_file = (
        workspace_db.query(ProjectFile)
        .filter(
            ProjectFile.project_id == 1,
            ProjectFile.parent_id == 13,
            ProjectFile.name == "new.py",
        )
        .one()
    )
    assert new_file.content == "print('new')\n"
    project_directory = sandbox.get_project_directory(1)
    assert (project_directory / "main.py").read_text(encoding="utf-8") == (
        "print('after')\n"
    )
    assert not (project_directory / "src" / "old.py").exists()
    assert (project_directory / "src" / "new.py").is_file()


def test_approved_file_sync_updates_and_deletes_workspace_mirror(
    workspace_db,
    tmp_path,
):
    sandbox = make_sandbox(tmp_path)
    service = ProjectWorkspaceService(
        workspace_db,
        sandbox_service=sandbox,
    )

    service.sync_file(1, 1, "main.py", "print('approved')\n")
    mirrored_file = sandbox.get_project_directory(1) / "main.py"
    assert mirrored_file.read_text(encoding="utf-8") == "print('approved')\n"

    service.sync_file(1, 1, "main.py", None)

    assert not mirrored_file.exists()


def test_docker_snapshot_runs_project_file_and_reports_failures(
    workspace_db,
    tmp_path,
    monkeypatch,
):
    disable_index_side_effects(monkeypatch)
    sandbox = make_sandbox(tmp_path)
    if not sandbox.is_available():
        pytest.skip("Docker sandbox image is unavailable.")

    hello_file = ProjectFile(
        id=15,
        project_id=1,
        name="hello.py",
        type="file",
        content="print('Hello World')\n",
        language="python",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    workspace_db.add(hello_file)
    workspace_db.commit()
    service = ProjectWorkspaceService(workspace_db, sandbox_service=sandbox)

    result, changes = service.execute_command(1, 1, "python hello.py")

    assert result.success is True
    assert result.exit_code == 0
    assert result.stdout.strip() == "Hello World"
    assert changes == []

    hello_file.content = "print('Hello AI')\n"
    workspace_db.commit()
    result, _ = service.execute_command(1, 1, "python hello.py")

    assert result.success is True
    assert result.stdout.strip() == "Hello AI"

    hello_file.content = "print('broken'\n"
    workspace_db.commit()
    result, changes = service.execute_command(1, 1, "python hello.py")

    assert result.success is False
    assert result.exit_code != 0
    assert "SyntaxError" in result.stderr
    assert changes == []
    assert hello_file.content == "print('broken'\n"

    hello_file.content = "print('fixed')\n"
    workspace_db.commit()
    result, _ = service.execute_command(1, 1, "python hello.py")

    assert result.success is True
    assert result.stdout.strip() == "fixed"


def test_failed_command_does_not_persist_partial_sandbox_changes(
    workspace_db,
    tmp_path,
    monkeypatch,
):
    sandbox = make_sandbox(tmp_path)

    def fail_after_write(_name, sandbox_workspace, _command, _timeout):
        (sandbox_workspace / "main.py").write_bytes(b"print('partial')\n")
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="failed",
        )

    monkeypatch.setattr(sandbox, "_run_container", fail_after_write)
    result, changes = ProjectWorkspaceService(
        workspace_db,
        sandbox_service=sandbox,
    ).execute_command(1, 1, "python main.py")

    assert result.success is False
    assert result.modified_files == []
    assert changes == []
    assert workspace_db.get(ProjectFile, 11).content == "print('before')\n"
    assert (sandbox.get_project_directory(1) / "main.py").read_text(
        encoding="utf-8"
    ) == "print('before')\n"


def test_database_snapshot_replaces_stale_workspace_before_execution(
    workspace_db,
    tmp_path,
    monkeypatch,
):
    sandbox = make_sandbox(tmp_path)
    mirrored_file = sandbox.get_project_directory(1) / "main.py"
    mirrored_file.write_bytes(b"changed outside the database\n")

    def modify_snapshot(_name, sandbox_workspace, _command, _timeout):
        assert mirrored_file.read_bytes() == b"print('before')\n"
        (sandbox_workspace / "main.py").write_bytes(b"sandbox change\n")
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="done",
            stderr="",
        )

    monkeypatch.setattr(sandbox, "_run_container", modify_snapshot)
    result, _changes = ProjectWorkspaceService(
        workspace_db,
        sandbox_service=sandbox,
    ).execute_command(1, 1, "python main.py")

    assert result.success is True
    assert workspace_db.get(ProjectFile, 11).content == "sandbox change\n"
    assert mirrored_file.read_bytes() == b"sandbox change\n"


def test_stale_sandbox_output_cannot_overwrite_a_newer_database_edit(
    workspace_db,
    tmp_path,
    monkeypatch,
):
    sandbox = make_sandbox(tmp_path)

    def concurrent_edit_then_run(_name, sandbox_workspace, _command, _timeout):
        current = workspace_db.get(ProjectFile, 11)
        current.content = "newer database edit\n"
        workspace_db.commit()
        (sandbox_workspace / "main.py").write_bytes(b"stale sandbox result\n")
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="done",
            stderr="",
        )

    monkeypatch.setattr(sandbox, "_run_container", concurrent_edit_then_run)
    with pytest.raises(ProjectWorkspaceSyncError, match="changed while sandbox"):
        ProjectWorkspaceService(
            workspace_db,
            sandbox_service=sandbox,
        ).execute_command(1, 1, "python main.py")

    assert workspace_db.get(ProjectFile, 11).content == "newer database edit\n"
    assert (sandbox.get_project_directory(1) / "main.py").read_bytes() == (
        b"print('before')\n"
    )


def test_project_workspace_service_rejects_non_owner_before_execution(
    workspace_db,
    tmp_path,
):
    sandbox = make_sandbox(tmp_path)

    with pytest.raises(ProjectWorkspaceSyncError, match="Project not found"):
        ProjectWorkspaceService(
            workspace_db,
            sandbox_service=sandbox,
        ).execute_command(2, 1, "python main.py")
