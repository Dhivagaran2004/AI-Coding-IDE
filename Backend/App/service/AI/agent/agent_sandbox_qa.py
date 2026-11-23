import re
from pathlib import PurePosixPath

from App.schema.ai_schema import CodeAction, TerminalContext
from App.service.AI.agent.agent_tools import AgentTools
from App.service.AI.code_action_service import MAX_RESULTING_FILE_CHARS
from App.service.project_workspace_service import (
    MAX_SANDBOX_PROJECT_BYTES,
    MAX_SANDBOX_PROJECT_FILES,
)
from App.service.sandbox_service import SandboxService


class AgentSandboxQAError(ValueError):
    pass


def build_sandbox_snapshot(
    tools: AgentTools,
    actions: list[CodeAction],
) -> dict[str, str]:
    files: dict[str, str] = {}
    total_bytes = 0
    for file_path in tools.list_project_files():
        if not AgentTools.is_safe_path(file_path):
            continue
        project_file = tools.resolve_file(file_path)
        content = project_file.content or ""
        if AgentTools.contains_sensitive_content(content):
            continue
        if len(content.encode("utf-8")) > MAX_RESULTING_FILE_CHARS:
            raise AgentSandboxQAError(
                f"Project file {file_path} exceeds the sandbox QA file-size limit."
            )
        files[file_path] = content
        total_bytes += len(content.encode("utf-8"))
        if len(files) > MAX_SANDBOX_PROJECT_FILES:
            raise AgentSandboxQAError("Project exceeds the sandbox file-count limit.")
        if total_bytes > MAX_SANDBOX_PROJECT_BYTES:
            raise AgentSandboxQAError("Project exceeds the sandbox size limit.")

    for action in actions:
        normalized_path = AgentTools._validate_path(action.file_path)
        if normalized_path != action.file_path:
            raise AgentSandboxQAError("A proposed action has an invalid project path.")
        if not AgentTools.is_safe_path(normalized_path):
            raise AgentSandboxQAError("A proposed action targets a protected file.")

        if action.operation == "create_file":
            if normalized_path in files:
                raise AgentSandboxQAError(
                    f"Cannot stage {normalized_path}: the file already exists."
                )
            files[normalized_path] = action.new_code
            total_bytes += len(action.new_code.encode("utf-8"))
            continue
        if action.operation != "replace":
            raise AgentSandboxQAError(
                f"Sandbox QA does not support {action.operation} actions."
            )
        current = files.get(normalized_path)
        if current is None:
            raise AgentSandboxQAError(
                f"Cannot stage {normalized_path}: the file was not found."
            )
        updated = _apply_replacement(current, action)
        total_bytes += len(updated.encode("utf-8")) - len(current.encode("utf-8"))
        files[normalized_path] = updated
        if total_bytes > MAX_SANDBOX_PROJECT_BYTES:
            raise AgentSandboxQAError("Project exceeds the sandbox size limit.")
    return files


def _apply_replacement(current: str, action: CodeAction) -> str:
    lines = current.splitlines(keepends=True)
    start_line = action.start_line
    end_line = action.end_line
    if start_line is None or end_line is None:
        raise AgentSandboxQAError("A replacement action has no line range.")
    if not lines:
        if (start_line, end_line, action.old_code) != (1, 1, ""):
            raise AgentSandboxQAError(
                f"Cannot stage {action.file_path}: the empty-file patch is invalid."
            )
        return action.new_code
    if end_line > len(lines):
        raise AgentSandboxQAError(
            f"Cannot stage {action.file_path}: the patch line range is out of bounds."
        )

    current_code = "".join(lines[start_line - 1:end_line])
    def normalize(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")

    if normalize(current_code) != normalize(action.old_code):
        expected = normalize(action.old_code)
        expected_lines = expected.splitlines(keepends=True)
        matches = [
            index
            for index in range(len(lines) - len(expected_lines) + 1)
            if expected_lines
            and normalize("".join(lines[index:index + len(expected_lines)]))
            == expected
        ]
        if len(matches) != 1:
            raise AgentSandboxQAError(
                f"Cannot stage {action.file_path}: the proposed source text does not "
                "match the project file."
            )
        start_line = matches[0] + 1
        end_line = matches[0] + len(expected_lines)
        current_code = "".join(lines[matches[0]:matches[0] + len(expected_lines)])

    replacement = normalize(action.new_code)
    newline = "\r\n" if "\r\n" in current else "\n"
    replacement = replacement.replace("\n", newline)
    if current_code.endswith(("\n", "\r")) and not replacement.endswith(("\n", "\r")):
        replacement += newline
    elif (
        not current_code.endswith(("\n", "\r"))
        and replacement.endswith(newline)
    ):
        replacement = replacement[:-len(newline)]
    return (
        "".join(lines[:start_line - 1])
        + replacement
        + "".join(lines[end_line:])
    )


def select_validation_command(
    files: dict[str, str],
    proposed_command: str | None,
) -> str | None:
    if proposed_command:
        return proposed_command

    paths = set(files)
    python_tests = any(
        AgentTools._is_test_path(path) and PurePosixPath(path).suffix == ".py"
        for path in paths
    )
    if python_tests:
        return "python -m pytest"

    package_manifest = files.get("package.json")
    if package_manifest:
        has_tests = bool(re.search(r'"test"\s*:', package_manifest))
        has_build = bool(re.search(r'"build"\s*:', package_manifest))
        if has_tests:
            return "npm test"
        if has_build:
            return "npm run build"

    if any(PurePosixPath(path).suffix == ".py" for path in paths):
        return "python -m compileall ."
    return None


def is_missing_dependency(result: TerminalContext) -> bool:
    output = f"{result.stdout}\n{result.stderr}".casefold()
    markers = (
        "no module named",
        "modulenotfounderror",
        "cannot find module",
        "cannot find package",
        "no matching distribution found",
        "npm error missing script",
        "command not found",
    )
    return any(marker in output for marker in markers)


def build_sandbox_qa_command(
    files: dict[str, str],
    validation_command: str,
) -> str:
    commands = []
    if "requirements.txt" in files:
        commands.append(
            "python -m pip install --disable-pip-version-check --no-index "
            "--find-links=/opt/wheelhouse -r requirements.txt"
        )
    if "package.json" in files:
        if "package-lock.json" in files or "npm-shrinkwrap.json" in files:
            commands.append(
                "npm ci --offline --cache=/opt/npm-cache"
            )
        else:
            commands.append(
                "npm install --offline --cache=/opt/npm-cache"
            )
    commands.append(validation_command)
    return " && ".join(commands)


def run_sandbox_validation(
    sandbox: SandboxService,
    project_id: int,
    files: dict[str, str],
    command: str,
    timeout_seconds: int = 120,
) -> TerminalContext:
    sandbox_command = build_sandbox_qa_command(files, command)
    result = sandbox.execute_snapshot(
        project_id=project_id,
        files=files,
        command=sandbox_command,
        timeout=timeout_seconds,
    )
    return TerminalContext(
        command=sandbox_command,
        exit_code=result.exit_code,
        stdout=result.stdout[:12000],
        stderr=result.stderr[:12000],
        success=result.success,
        executed=not (
            result.exit_code == 127
            and result.stderr == "Docker is not installed or unreachable."
        ),
        timeout=result.timeout,
        created_files=result.created_files,
        modified_files=result.modified_files,
        deleted_files=result.deleted_files,
    )
