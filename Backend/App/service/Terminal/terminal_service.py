from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath

from App.service.Terminal.terminal_security import (
    parse_terminal_command,
)


MAX_TERMINAL_OUTPUT_CHARS = 12000
MAX_TERMINAL_FILE_BYTES = 500000
MAX_TERMINAL_LIST_ENTRIES = 200


@dataclass
class TerminalResult:
    exit_code: int
    stdout: str
    stderr: str
    success: bool


class TerminalService:
    """Serve a bounded set of read-only commands inside project workspaces."""

    def __init__(self, workspace_root: str = "workspaces"):
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def get_project_directory(self, project_id: int) -> Path:
        project_directory = (self.workspace_root / str(project_id)).resolve()
        try:
            project_directory.relative_to(self.workspace_root)
        except ValueError as error:
            raise FileNotFoundError(
                "Project workspace is outside the allowed root."
            ) from error

        project_directory.mkdir(parents=True, exist_ok=True)
        project_directory = project_directory.resolve()
        try:
            project_directory.relative_to(self.workspace_root)
        except ValueError as error:
            raise FileNotFoundError(
                "Project workspace is outside the allowed root."
            ) from error
        return project_directory

    def validate_project_directory(self, project_id: int) -> Path:
        project_directory = self.get_project_directory(project_id)
        if not project_directory.exists():
            raise FileNotFoundError("Project workspace does not exist.")
        if not project_directory.is_dir():
            raise NotADirectoryError("Project workspace is not a directory.")
        return project_directory

    def _resolve_workspace_path(
        self,
        project_directory: Path,
        raw_path: str,
    ) -> Path:
        windows_path = PureWindowsPath(raw_path)
        relative_path = Path(raw_path)
        if (
            not raw_path
            or relative_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or ".." in relative_path.parts
            or ".." in windows_path.parts
            or raw_path.startswith("-")
        ):
            raise ValueError("Only relative paths inside the project are allowed.")

        try:
            resolved_path = (project_directory / relative_path).resolve(
                strict=True
            )
            resolved_path.relative_to(project_directory)
        except (OSError, ValueError) as error:
            raise ValueError(
                "Path is missing or outside the project workspace."
            ) from error
        return resolved_path

    @staticmethod
    def _truncate_output(value: str) -> str:
        if len(value) <= MAX_TERMINAL_OUTPUT_CHARS:
            return value
        return (
            "[output truncated]\n"
            + value[-(MAX_TERMINAL_OUTPUT_CHARS - 20) :]
        )

    def _list_path(self, project_directory: Path, raw_path: str) -> str:
        target = self._resolve_workspace_path(project_directory, raw_path)
        if not target.is_dir():
            return target.name

        entries: list[str] = []
        truncated = False
        with os.scandir(target) as iterator:
            for entry in iterator:
                if len(entries) >= MAX_TERMINAL_LIST_ENTRIES:
                    truncated = True
                    break
                if entry.is_symlink():
                    entries.append(f"{entry.name} [symlink]")
                else:
                    entries.append(
                        entry.name + ("/" if entry.is_dir(follow_symlinks=False) else "")
                    )

        entries.sort()
        output = "\n".join(entries)
        if truncated:
            output = f"{output}\n[listing truncated]"
        return self._truncate_output(output)

    def _read_text_file(self, project_directory: Path, raw_path: str) -> str:
        target = self._resolve_workspace_path(project_directory, raw_path)
        if not target.is_file():
            raise ValueError("The requested project path is not a file.")

        with target.open("rb") as source:
            content = source.read(MAX_TERMINAL_FILE_BYTES + 1)
        if len(content) > MAX_TERMINAL_FILE_BYTES:
            raise ValueError("The requested file exceeds the terminal read limit.")
        if b"\0" in content:
            raise ValueError("Binary files cannot be displayed in the terminal.")
        return content.decode("utf-8", errors="replace")

    def _execute_safe_command(
        self,
        project_directory: Path,
        arguments: list[str],
    ) -> str:
        command = arguments[0].lower()
        parameters = arguments[1:]

        if command == "help":
            if parameters:
                raise ValueError("The help command does not accept arguments.")
            return (
                "Supported read-only workspace commands:\n"
                "pwd\nls [relative-path]\ncat <relative-file>\n"
                "head [-n count] <relative-file>\n"
                "tail [-n count] <relative-file>"
            )

        if command == "pwd":
            if parameters:
                raise ValueError("The pwd command does not accept arguments.")
            return "."

        if command in {"ls", "dir"}:
            if len(parameters) > 1:
                raise ValueError("List one project-relative path at a time.")
            return self._list_path(
                project_directory,
                parameters[0] if parameters else ".",
            )

        if command in {"cat", "type"}:
            if len(parameters) != 1:
                raise ValueError("Read one project-relative file at a time.")
            return self._truncate_output(
                self._read_text_file(project_directory, parameters[0])
            )

        if command in {"head", "tail"}:
            if len(parameters) == 1:
                line_count = 10
                raw_path = parameters[0]
            elif len(parameters) == 3 and parameters[0] == "-n":
                try:
                    line_count = int(parameters[1])
                except ValueError as error:
                    raise ValueError("Line count must be an integer.") from error
                raw_path = parameters[2]
            else:
                raise ValueError(
                    f"Use {command} [-n count] <relative-file>."
                )
            if not 1 <= line_count <= 200:
                raise ValueError("Line count must be between 1 and 200.")

            lines = self._read_text_file(project_directory, raw_path).splitlines()
            selected = lines[:line_count] if command == "head" else lines[-line_count:]
            return self._truncate_output("\n".join(selected))

        raise ValueError("Command is not allowlisted.")

    def execute_command(
        self,
        project_id: int,
        command: str,
        timeout_seconds: int | None = None,
    ) -> TerminalResult:
        arguments = parse_terminal_command(command)
        project_directory = self.validate_project_directory(project_id)
        try:
            stdout = self._execute_safe_command(
                project_directory,
                arguments,
            )
        except ValueError as error:
            return TerminalResult(
                exit_code=1,
                stdout="",
                stderr=str(error),
                success=False,
            )
        except OSError:
            return TerminalResult(
                exit_code=1,
                stdout="",
                stderr="Unable to access the requested project path.",
                success=False,
            )
        return TerminalResult(
            exit_code=0,
            stdout=stdout,
            stderr="",
            success=True,
        )

    def stop_command(self, project_id: int) -> bool:
        raise ValueError("No running command found for this project.")
