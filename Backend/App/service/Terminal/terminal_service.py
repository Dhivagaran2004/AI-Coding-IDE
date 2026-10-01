from pathlib import Path
import subprocess
from dataclasses import dataclass
from threading import Lock


@dataclass
class TerminalResult:
    """
    Result returned after executing a terminal command.
    """

    exit_code: int
    stdout: str
    stderr: str
    success: bool


class TerminalService:
    """
    Service responsible for terminal workspace management,
    command execution, and running-process management.
    """

    def __init__(self, workspace_root: str = "workspaces"):
        self.workspace_root = Path(workspace_root).resolve()

        self.workspace_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        # Store currently running processes by project ID.
        self.running_processes: dict[int, subprocess.Popen] = {}

        self.process_lock = Lock()

    # =========================================================
    # Project Workspace
    # =========================================================

    def get_project_directory(
        self,
        project_id: int,
    ) -> Path:

        project_directory = (
            self.workspace_root / str(project_id)
        )

        resolved_directory = project_directory.resolve()
        try:
            resolved_directory.relative_to(self.workspace_root)
        except ValueError as error:
            raise FileNotFoundError(
                "Project workspace is outside the allowed root."
            ) from error

        resolved_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        resolved_directory = resolved_directory.resolve()
        try:
            resolved_directory.relative_to(self.workspace_root)
        except ValueError as error:
            raise FileNotFoundError(
                "Project workspace is outside the allowed root."
            ) from error

        return resolved_directory

    # =========================================================
    # Validate Project Workspace
    # =========================================================

    def validate_project_directory(
        self,
        project_id: int,
    ) -> Path:

        project_directory = self.get_project_directory(
            project_id
        )

        if not project_directory.exists():
            raise FileNotFoundError(
                "Project workspace does not exist."
            )

        if not project_directory.is_dir():
            raise NotADirectoryError(
                "Project workspace is not a directory."
            )

        return project_directory

    # =========================================================
    # Execute Command
    # =========================================================

    def execute_command(
        self,
        project_id: int,
        command: str,
        timeout_seconds: int | None = None,
    ) -> TerminalResult:

        if not command or not command.strip():
            raise ValueError(
                "Command cannot be empty."
            )

        project_directory = (
            self.validate_project_directory(
                project_id
            )
        )

        process = subprocess.Popen(
            command,
            cwd=project_directory,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # Register running process.
        with self.process_lock:
            self.running_processes[project_id] = process

        try:

            try:
                stdout, stderr = process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                timeout_message = (
                    f"Command timed out after {timeout_seconds} seconds."
                )
                stderr = f"{stderr or ''}\n{timeout_message}".strip()
                return TerminalResult(
                    exit_code=-1,
                    stdout=(stdout or "")[-12000:],
                    stderr=stderr[-12000:],
                    success=False,
                )

            exit_code = process.returncode

            return TerminalResult(
                exit_code=exit_code,
                stdout=stdout[-12000:],
                stderr=stderr[-12000:],
                success=exit_code == 0,
            )

        finally:

            # Remove process after completion.
            with self.process_lock:

                current_process = (
                    self.running_processes.get(
                        project_id
                    )
                )

                if current_process is process:

                    self.running_processes.pop(
                        project_id,
                        None,
                    )

    # =========================================================
    # Stop Running Command
    # =========================================================

    def stop_command(
        self,
        project_id: int,
    ) -> bool:

        with self.process_lock:

            process = (
                self.running_processes.get(
                    project_id
                )
            )

        if process is None:

            raise ValueError(
                "No running command found for this project."
            )

        # Check whether process already finished.
        if process.poll() is not None:

            with self.process_lock:

                self.running_processes.pop(
                    project_id,
                    None,
                )

            raise ValueError(
                "The command has already finished."
            )

        try:

            # Windows: terminate the process.
            process.terminate()

            try:

                process.wait(
                    timeout=3
                )

            except subprocess.TimeoutExpired:

                process.kill()

                process.wait(
                    timeout=3
                )

        finally:

            with self.process_lock:

                self.running_processes.pop(
                    project_id,
                    None,
                )

        return True