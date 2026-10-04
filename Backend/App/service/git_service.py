from pathlib import Path
import os
import subprocess


MAX_GIT_DIFF_CHARS = 100000


class GitCommandError(RuntimeError):
    pass


class GitService:
    """Read-only Git inspection scoped to one project workspace."""

    def __init__(self, project_directory: Path, timeout_seconds: int = 10):
        self.project_directory = project_directory
        self.timeout_seconds = timeout_seconds

    def _run(self, *arguments: str) -> str:
        env = os.environ.copy()
        env["GIT_OPTIONAL_LOCKS"] = "0"
        env["GIT_TERMINAL_PROMPT"] = "0"
        try:
            result = subprocess.run(
                [
                    "git",
                    "--no-pager",
                    "-C",
                    str(self.project_directory),
                    *arguments,
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitCommandError("Git inspection is unavailable.") from error
        if result.returncode != 0:
            raise GitCommandError("Workspace is not a readable Git repository.")
        return result.stdout

    def status(self) -> dict[str, object]:
        output = self._run("status", "--porcelain=v1", "-z", "--branch")
        entries = output.split("\0")
        branch = self._run("branch", "--show-current").strip()
        if entries and entries[0].startswith("## "):
            branch_line = entries.pop(0)[3:]
            if not branch:
                branch = branch_line.strip()

        changed_files: list[dict[str, object]] = []
        staged_files: list[str] = []
        untracked_files: list[str] = []
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            if len(entry) < 4:
                raise GitCommandError("Git returned an invalid status response.")
            xy = entry[:2]
            path = entry[3:]
            if "R" in xy or "C" in xy:
                if index < len(entries) and entries[index]:
                    path = f"{entries[index]} -> {path}"
                    index += 1
            if xy == "??":
                status = "U"
                untracked_files.append(path)
            elif "U" in xy:
                status = "C"
            elif "D" in xy:
                status = "D"
            elif "A" in xy:
                status = "A"
            elif "R" in xy:
                status = "R"
            else:
                status = "M"
            staged = xy[0] != " "
            unstaged = xy[1] != " "
            if staged:
                staged_files.append(path)
            changed_files.append(
                {
                    "path": path,
                    "status": status,
                    "index_status": xy[0],
                    "worktree_status": xy[1],
                    "staged": staged,
                    "unstaged": unstaged,
                }
            )
        return {
            "branch": branch,
            "changed_files": changed_files,
            "staged_files": staged_files,
            "untracked_files": untracked_files,
        }

    def diff(self, staged: bool = False) -> dict[str, object]:
        arguments = [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--unified=3",
        ]
        if staged:
            arguments.append("--cached")
        output = self._run(*arguments)
        return {
            "diff": output[:MAX_GIT_DIFF_CHARS],
            "truncated": len(output) > MAX_GIT_DIFF_CHARS,
        }
