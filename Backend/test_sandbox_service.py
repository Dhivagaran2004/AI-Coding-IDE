import subprocess

import pytest

from App.service.sandbox_service import SandboxService


def test_sandbox_timeout_is_capped(tmp_path):
    service = SandboxService(
        workspace_root=str(tmp_path / "workspaces"),
        sandbox_root=str(tmp_path / "temporary" / "sandbox" / "workspace"),
    )

    assert service._normalize_timeout(500) == 120
    assert service._normalize_timeout(30) == 30


def test_sandbox_timeout_reports_partial_output_without_syncing_changes(
    tmp_path,
    monkeypatch,
):
    workspace_root = tmp_path / "workspaces"
    project_dir = workspace_root / "1"
    project_dir.mkdir(parents=True)
    project_file = project_dir / "hello.txt"
    project_file.write_text("before\n", encoding="utf-8")
    service = SandboxService(
        workspace_root=str(workspace_root),
        sandbox_root=str(tmp_path / "temporary" / "sandbox" / "workspace"),
    )

    def timeout_container(_container_name, _workspace_path, _command, timeout):
        raise subprocess.TimeoutExpired(
            cmd="docker run",
            timeout=timeout,
            output=b"partial output\n",
            stderr=b"",
        )

    monkeypatch.setattr(service, "_run_container", timeout_container)
    monkeypatch.setattr(service, "_cleanup_container", lambda _container_name: None)

    result = service.execute(project_id=1, command="echo hello", timeout=30)

    assert result.success is False
    assert result.timeout is True
    assert result.exit_code == 124
    assert result.stdout == "partial output\n"
    assert "timed out after 30 seconds" in result.stderr
    assert project_file.read_text(encoding="utf-8") == "before\n"


def test_sandbox_execute_syncs_changes(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspaces"
    project_dir = workspace_root / "1"
    project_dir.mkdir(parents=True)
    project_file = project_dir / "hello.txt"
    project_file.write_text("before\n", encoding="utf-8")

    service = SandboxService(
        workspace_root=str(workspace_root),
        sandbox_root=str(tmp_path / "temporary" / "sandbox" / "workspace"),
    )

    def fake_run_container(_container_name, workspace_path, _command, _timeout):
        target = workspace_path / "hello.txt"
        target.write_text("after\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="done",
            stderr="",
        )

    monkeypatch.setattr(service, "_run_container", fake_run_container)

    result = service.execute(project_id=1, command="echo hello", timeout=30)

    assert result.success is True
    assert result.modified_files == ["hello.txt"]
    assert project_file.read_text(encoding="utf-8") == "after\n"


def test_sandbox_reports_unavailable_docker(tmp_path):
    service = SandboxService(
        workspace_root=str(tmp_path / "workspaces"),
        sandbox_root=str(tmp_path / "temporary" / "sandbox" / "workspace"),
        docker_binary="missing-docker-binary",
    )

    result = service.execute(project_id=1, command="echo hello")

    assert result.exit_code == 127
    assert result.success is False
    assert "Docker is not installed" in result.stderr


def test_sandbox_rejects_symlink_replacement_without_deleting_original(
    tmp_path,
    monkeypatch,
):
    workspace_root = tmp_path / "workspaces"
    project_dir = workspace_root / "1"
    project_dir.mkdir(parents=True)
    project_file = project_dir / "hello.txt"
    project_file.write_text("before\n", encoding="utf-8")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside\n", encoding="utf-8")

    service = SandboxService(
        workspace_root=str(workspace_root),
        sandbox_root=str(tmp_path / "temporary" / "sandbox" / "workspace"),
    )

    def fake_run_container(_container_name, workspace_path, _command, _timeout):
        target = workspace_path / "hello.txt"
        target.unlink()
        try:
            target.symlink_to(outside_file)
        except OSError as error:
            pytest.skip(f"Creating a symlink is unavailable: {error}")
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="done",
            stderr="",
        )

    monkeypatch.setattr(service, "_run_container", fake_run_container)

    result = service.execute(project_id=1, command="echo hello", timeout=30)

    assert result.success is False
    assert "symbolic links" in result.stderr
    assert result.deleted_files == []
    assert project_file.read_text(encoding="utf-8") == "before\n"
