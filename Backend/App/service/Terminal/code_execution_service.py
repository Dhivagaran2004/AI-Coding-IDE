from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore, Thread
from uuid import uuid4

from App.models.project_file import ProjectFile
from App.service.AI.index.repository_index_service import (
    RepositoryIndexService,
)


@dataclass
class CodeExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    success: bool


@dataclass
class SnapshotFile:
    path: str
    content: str


class ProjectFileNotFound(ValueError):
    pass


class UnsupportedSourceFile(ValueError):
    pass


class ExecutionQueueFull(RuntimeError):
    pass


class SandboxUnavailable(RuntimeError):
    pass


class DockerSandboxRunner:
    LANGUAGE_IMAGES = {
        ".py": "python:3.13-alpine",
        ".js": "node:22-alpine",
        ".mjs": "node:22-alpine",
        ".cjs": "node:22-alpine",
        ".ts": "denoland/deno:alpine-2.2.0",
        ".tsx": "denoland/deno:alpine-2.2.0",
        ".java": "eclipse-temurin:21-jdk-alpine",
        ".cpp": "gcc:14",
        ".cc": "gcc:14",
        ".cxx": "gcc:14",
        ".go": "golang:1.24-alpine",
    }

    OUTPUT_LIMIT = 65536

    def __init__(
        self,
        docker_executable: str = "docker",
        timeout_seconds: int = 20,
    ):
        self.docker_executable = docker_executable
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        snapshot: list[SnapshotFile],
        entry_path: str,
    ) -> CodeExecutionResult:
        extension = Path(entry_path).suffix.lower()
        image = self.LANGUAGE_IMAGES.get(extension)
        if image is None:
            raise UnsupportedSourceFile("Unsupported source file language.")

        container_name = f"ide-execution-{uuid4().hex}"
        with TemporaryDirectory(prefix="ide-execution-") as directory:
            workspace = Path(directory)
            for snapshot_file in snapshot:
                target = workspace.joinpath(
                    *PurePosixPath(snapshot_file.path).parts
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(snapshot_file.content, encoding="utf-8")

            command = self._language_command(extension, entry_path, snapshot)
            docker_command = [
                self.docker_executable,
                "run",
                "--rm",
                "--pull=missing",
                "--name",
                container_name,
                "--network",
                "none",
                "--memory=256m",
                "--cpus=0.5",
                "--pids-limit=64",
                "--read-only",
                "--user=65534:65534",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--tmpfs",
                "/tmp:rw,exec,nosuid,nodev,size=64m",
                "--workdir",
                "/workspace",
                "--mount",
                f"type=bind,source={workspace.resolve()},target=/workspace,readonly",
                "--env",
                "HOME=/tmp",
                image,
                *command,
            ]

            try:
                process = subprocess.Popen(
                    docker_command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise SandboxUnavailable(
                    "Docker is unavailable on the server."
                ) from exc
            except OSError as exc:
                raise SandboxUnavailable(
                    "The sandbox could not be started."
                ) from exc

            stdout = bytearray()
            stderr = bytearray()
            stdout_thread = Thread(
                target=self._capture_output,
                args=(process.stdout, stdout),
                daemon=True,
            )
            stderr_thread = Thread(
                target=self._capture_output,
                args=(process.stderr, stderr),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()

            timed_out = False
            try:
                exit_code = process.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill_container(container_name)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                exit_code = 124
            finally:
                stdout_thread.join(timeout=3)
                stderr_thread.join(timeout=3)

        stdout_text = self._decode_output(stdout)
        stderr_text = self._decode_output(stderr)
        if timed_out:
            return CodeExecutionResult(
                exit_code=124,
                stdout=stdout_text,
                stderr=(stderr_text + "\nExecution exceeded the time limit.").strip(),
                success=False,
            )

        if exit_code == 125 and self._is_docker_error(stderr_text):
            raise SandboxUnavailable(
                "The sandbox runtime is unavailable or its language image could not be started."
            )

        return CodeExecutionResult(
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            success=exit_code == 0,
        )

    @staticmethod
    def _language_command(
        extension: str,
        entry_path: str,
        snapshot: list[SnapshotFile],
    ) -> list[str]:
        source = f"/workspace/{entry_path}"
        if extension == ".py":
            return ["python", source]
        if extension in {".js", ".mjs", ".cjs"}:
            return ["node", source]
        if extension in {".ts", ".tsx"}:
            return [
                "deno",
                "run",
                "--allow-read=/workspace",
                "--deny-net",
                "--no-prompt",
                source,
            ]
        if extension == ".java":
            entry = next(
                item.content
                for item in snapshot
                if item.path == entry_path
            )
            class_match = re.search(
                r"\b(?:public\s+)?(?:class|record|enum)\s+([A-Za-z_$][\w$]*)",
                entry,
            )
            if class_match is None:
                raise UnsupportedSourceFile(
                    "Java source must declare a class, record, or enum."
                )
            class_name = class_match.group(1)
            return [
                "sh",
                "-lc",
                f"javac -d /tmp {shlex.quote(source)} && java -cp /tmp {shlex.quote(class_name)}",
            ]
        if extension in {".cpp", ".cc", ".cxx"}:
            return [
                "sh",
                "-lc",
                f"g++ -std=c++20 {shlex.quote(source)} -o /tmp/ide-program && /tmp/ide-program",
            ]
        if extension == ".go":
            return ["go", "run", source]
        raise UnsupportedSourceFile("Unsupported source file language.")

    def _kill_container(self, container_name: str) -> None:
        try:
            subprocess.run(
                [self.docker_executable, "kill", container_name],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return

    @classmethod
    def _capture_output(cls, stream, output: bytearray) -> None:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            remaining = cls.OUTPUT_LIMIT - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    output.extend(b"\n[output truncated]")

    @classmethod
    def _decode_output(cls, output: bytearray) -> str:
        return output.decode("utf-8", errors="replace")

    @classmethod
    def _limit_output(cls, output: str | bytes | None) -> str:
        if output is None:
            return ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        if len(output) <= cls.OUTPUT_LIMIT:
            return output
        return output[: cls.OUTPUT_LIMIT] + "\n[output truncated]"

    @staticmethod
    def _is_docker_error(stderr: str) -> bool:
        error_text = stderr.lower()
        return any(
            marker in error_text
            for marker in (
                "cannot connect to the docker daemon",
                "failed to connect to the docker api",
                "is the docker daemon running",
                "error response from daemon",
                "pull access denied",
                "manifest unknown",
                "no such image",
            )
        )


class CodeExecutionService:
    MAX_SOURCE_CHARS = 100_000

    def __init__(
        self,
        runner: DockerSandboxRunner | None = None,
        max_workers: int = 2,
        max_queued_jobs: int = 8,
    ):
        if max_workers < 1 or max_queued_jobs < 0:
            raise ValueError("Invalid execution queue capacity.")
        self.runner = runner or DockerSandboxRunner()
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="ide-code-execution",
        )
        self.available_slots = BoundedSemaphore(
            max_workers + max_queued_jobs
        )

    def execute_file(
        self,
        project_id: int,
        project_files: list[ProjectFile],
        file_id: int,
        code: str,
    ) -> CodeExecutionResult:
        if not code.strip():
            raise ValueError("Source code cannot be empty.")
        if len(code) > self.MAX_SOURCE_CHARS:
            raise ValueError("Source code exceeds the maximum size.")

        snapshot, entry_path = self._prepare_snapshot(
            project_id,
            project_files,
            file_id,
            code,
        )
        if not self.available_slots.acquire(blocking=False):
            raise ExecutionQueueFull("The code execution queue is full.")

        try:
            future = self.executor.submit(
                self.runner.run,
                snapshot,
                entry_path,
            )
        except Exception:
            self.available_slots.release()
            raise

        future.add_done_callback(
            lambda _future: self.available_slots.release()
        )
        return future.result()

    @classmethod
    def _prepare_snapshot(
        cls,
        project_id: int,
        project_files: list[ProjectFile],
        file_id: int,
        code: str,
    ) -> tuple[list[SnapshotFile], str]:
        files_by_id = {
            project_file.id: project_file
            for project_file in project_files
            if project_file.project_id == project_id
        }
        selected = files_by_id.get(file_id)
        if selected is None:
            raise ProjectFileNotFound("File not found in this project.")
        if selected.type != "file" or not RepositoryIndexService.should_index_file(selected):
            raise UnsupportedSourceFile(
                "This file cannot be executed."
            )

        extension = Path(selected.name).suffix.lower()
        if extension not in DockerSandboxRunner.LANGUAGE_IMAGES:
            raise UnsupportedSourceFile(
                "The selected file language is not supported."
            )

        try:
            entry_path = cls._get_safe_project_path(
                selected,
                files_by_id,
            )
        except ValueError as error:
            raise UnsupportedSourceFile(
                "The selected file path is invalid."
            ) from error

        return [SnapshotFile(path=entry_path, content=code)], entry_path

    @staticmethod
    def _get_safe_project_path(
        project_file: ProjectFile,
        files_by_id: dict[int, ProjectFile],
    ) -> str:
        parts: list[str] = []
        current = project_file
        visited: set[int] = set()

        while current is not None:
            if current.id in visited:
                raise ValueError("Project file tree contains a cycle.")
            visited.add(current.id)

            name = (current.name or "").strip()
            if (
                not name
                or name in {".", ".."}
                or "/" in name
                or "\\" in name
                or "\x00" in name
                or ":" in name
            ):
                raise ValueError("Project path contains an invalid name.")
            parts.append(name)

            if current.parent_id is None:
                break
            current = files_by_id.get(current.parent_id)
            if current is None or current.type != "folder":
                raise ValueError("Project path has an invalid parent.")

        return PurePosixPath(*reversed(parts)).as_posix()