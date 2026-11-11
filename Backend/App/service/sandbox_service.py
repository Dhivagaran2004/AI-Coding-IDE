import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable, Mapping
from pathlib import PurePosixPath, PureWindowsPath


@dataclass
class SandboxExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    success: bool
    timeout: bool = False
    created_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)


class SandboxService:
    """Execute project commands inside an isolated Docker workspace."""

    DEFAULT_TIMEOUT_SECONDS = 30
    MAX_TIMEOUT_SECONDS = 120
    DEFAULT_CPU_LIMIT = "1"
    DEFAULT_MEMORY_LIMIT = "512m"
    DEFAULT_PID_LIMIT = 100
    DEFAULT_DOCKER_IMAGE = "ai-coding-ide-sandbox:latest"

    def __init__(
        self,
        workspace_root: str = "workspaces",
        sandbox_root: str | None = None,
        docker_image: str = DEFAULT_DOCKER_IMAGE,
        docker_binary: str = "docker",
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        if sandbox_root is None:
            sandbox_root = str(self.workspace_root.parent / "temporary" / "sandbox" / "workspace")
        self.sandbox_root = Path(sandbox_root).resolve()
        self.sandbox_root.mkdir(parents=True, exist_ok=True)

        self.docker_image = docker_image
        self.docker_binary = docker_binary

    def get_project_directory(self, project_id: int) -> Path:
        project_directory = (self.workspace_root / str(project_id)).resolve()
        try:
            project_directory.relative_to(self.workspace_root)
        except ValueError as error:
            raise FileNotFoundError("Project workspace is outside the allowed root.") from error

        project_directory.mkdir(parents=True, exist_ok=True)
        return project_directory

    def is_available(self) -> bool:
        try:
            completed = subprocess.run(
                [self.docker_binary, "image", "inspect", self.docker_image],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return False
        return completed.returncode == 0

    def _normalize_timeout(self, timeout: int | None) -> int:
        requested = self.DEFAULT_TIMEOUT_SECONDS if timeout is None else int(timeout)
        if requested <= 0:
            raise ValueError("Timeout must be greater than zero seconds.")
        return min(requested, self.MAX_TIMEOUT_SECONDS)

    def _collect_file_hashes(self, root: Path) -> dict[str, str]:
        hashes: dict[str, str] = {}
        if not root.exists():
            return hashes

        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError("Sandbox workspaces cannot contain symbolic links.")
            if not path.is_file():
                continue
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError:
                continue
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                hashes[relative] = digest
        return hashes

    def _detect_workspace_changes(self, original: Path, sandbox: Path) -> tuple[list[str], list[str], list[str]]:
        original_hashes = self._collect_file_hashes(original)
        sandbox_hashes = self._collect_file_hashes(sandbox)

        created = sorted(
            path for path in sandbox_hashes if path not in original_hashes
        )
        modified = sorted(
            path for path in sandbox_hashes
            if path in original_hashes and sandbox_hashes[path] != original_hashes[path]
        )
        deleted = sorted(
            path for path in original_hashes if path not in sandbox_hashes
        )
        return created, modified, deleted

    def _copy_workspace_changes(self, original: Path, sandbox: Path, *, created: list[str], modified: list[str], deleted: list[str]) -> None:
        for relative_path in created + modified:
            source = sandbox / relative_path
            destination = original / relative_path
            try:
                source.resolve().relative_to(sandbox.resolve())
                destination.resolve().relative_to(original.resolve())
            except ValueError:
                raise ValueError("Sandbox change escaped the project workspace.")
            if source.is_symlink() or destination.is_symlink():
                raise ValueError("Sandbox changes cannot contain symbolic links.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.exists() and source.is_file():
                shutil.copy2(source, destination)

        for relative_path in deleted:
            target = original / relative_path
            try:
                target.resolve().relative_to(original.resolve())
            except ValueError:
                if not target.is_symlink():
                    raise ValueError("Sandbox deletion escaped the project workspace.")
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()

    def _create_temp_workspace(self, project_directory: Path) -> Path:
        workspace_dir = Path(tempfile.mkdtemp(prefix="sandbox_", dir=str(self.sandbox_root)))
        shutil.copytree(project_directory, workspace_dir, dirs_exist_ok=True)
        return workspace_dir

    def _cleanup_container(self, container_name: str) -> None:
        if not container_name:
            return

        try:
            subprocess.run(
                [self.docker_binary, "rm", "-f", container_name],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return

    def _run_container(self, container_name: str, workspace_path: Path, command: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        container_command = [
            self.docker_binary,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network=none",
            f"--cpus={self.DEFAULT_CPU_LIMIT}",
            f"--memory={self.DEFAULT_MEMORY_LIMIT}",
            f"--pids-limit={self.DEFAULT_PID_LIMIT}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--workdir=/workspace",
            "-v",
            f"{workspace_path}:/workspace:rw",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            self.docker_image,
            "bash",
            "-lc",
            command,
        ]

        return subprocess.run(
            container_command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )

    def _sync_changes(self, original: Path, sandbox: Path) -> tuple[list[str], list[str], list[str]]:
        created, modified, deleted = self._detect_workspace_changes(original, sandbox)
        if created or modified or deleted:
            self._copy_workspace_changes(original, sandbox, created=created, modified=modified, deleted=deleted)
        return created, modified, deleted

    def execute(
        self,
        project_id: int,
        command: str,
        timeout: int | None = None,
    ) -> SandboxExecutionResult:
        if not command or not command.strip():
            raise ValueError("Command cannot be empty.")

        timeout_seconds = self._normalize_timeout(timeout)
        project_directory = self.get_project_directory(project_id)
        if not project_directory.exists():
            raise FileNotFoundError("Project workspace does not exist.")

        return self.execute_directory(project_directory, command, timeout)

    def execute_snapshot(
        self,
        project_id: int,
        files: Mapping[str, str],
        command: str,
        timeout: int | None = None,
        on_success: Callable[[Path, SandboxExecutionResult], None] | None = None,
    ) -> SandboxExecutionResult:
        """Run against an isolated snapshot instead of a host workspace directory."""
        self._normalize_timeout(timeout)
        snapshot = Path(tempfile.mkdtemp(prefix=f"project_{project_id}_", dir=str(self.sandbox_root)))
        try:
            for relative_path, content in files.items():
                path = PurePosixPath(relative_path)
                if (
                    not relative_path
                    or "\x00" in relative_path
                    or "\\" in relative_path
                    or PureWindowsPath(relative_path).drive
                    or path.is_absolute()
                    or any(part in {"", ".", ".."} for part in path.parts)
                ):
                    raise ValueError("Invalid project snapshot path.")
                target = snapshot.joinpath(*relative_path.split("/"))
                resolved = target.resolve()
                try:
                    resolved.relative_to(snapshot.resolve())
                except ValueError as error:
                    raise ValueError("Project snapshot path escaped its workspace.") from error
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            result = self.execute_directory(snapshot, command, timeout)
            if result.success and on_success is not None:
                on_success(snapshot, result)
            return result
        finally:
            shutil.rmtree(snapshot, ignore_errors=True)

    def execute_directory(
        self,
        project_directory: Path,
        command: str,
        timeout: int | None = None,
    ) -> SandboxExecutionResult:
        if not command or not command.strip():
            raise ValueError("Command cannot be empty.")
        timeout_seconds = self._normalize_timeout(timeout)
        project_directory = project_directory.resolve()
        if not project_directory.exists() or not project_directory.is_dir():
            raise FileNotFoundError("Project workspace does not exist.")

        sandbox_workspace = self._create_temp_workspace(project_directory)
        container_name = f"ai-ide-sandbox-{uuid.uuid4().hex[:12]}"

        try:
            try:
                completed = self._run_container(
                    container_name,
                    sandbox_workspace,
                    command,
                    timeout_seconds,
                )
                stdout = completed.stdout or ""
                stderr = completed.stderr or ""
                exit_code = completed.returncode
                timed_out = False
            except FileNotFoundError as error:
                return SandboxExecutionResult(
                    exit_code=127,
                    stdout="",
                    stderr="Docker is not installed or unreachable.",
                    success=False,
                )
            except subprocess.TimeoutExpired as error:
                stdout = error.stdout or ""
                stderr = error.stderr or ""
                if isinstance(stdout, bytes):
                    stdout = stdout.decode("utf-8", errors="replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                timed_out = True
                exit_code = 124
                if not stderr:
                    stderr = f"Command timed out after {timeout_seconds} seconds."
                self._cleanup_container(container_name)
                return SandboxExecutionResult(
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    success=False,
                    timeout=True,
                )

            if completed.returncode == 0:
                try:
                    created, modified, deleted = self._sync_changes(
                        project_directory,
                        sandbox_workspace,
                    )
                except ValueError as error:
                    return SandboxExecutionResult(
                        exit_code=1,
                        stdout=stdout,
                        stderr=f"Sandbox changes were not applied: {error}",
                        success=False,
                    )
            else:
                created = []
                modified = []
                deleted = []

            return SandboxExecutionResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                success=exit_code == 0,
                created_files=created,
                modified_files=modified,
                deleted_files=deleted,
                timeout=timed_out,
            )
        finally:
            self._cleanup_container(container_name)
            if sandbox_workspace.exists():
                shutil.rmtree(sandbox_workspace, ignore_errors=True)

    execute_command = execute
