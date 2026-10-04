import subprocess

from App.service.git_service import GitService


def git(directory, *arguments):
    return subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def test_git_status_and_diff_are_read_only_and_report_file_states(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.test")
    git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "modified.py").write_text("value = 1\n", encoding="utf-8")
    git(tmp_path, "add", "modified.py")
    git(tmp_path, "commit", "-m", "initial")

    (tmp_path / "modified.py").write_text("value = 2\n", encoding="utf-8")
    (tmp_path / "staged.py").write_text("staged = True\n", encoding="utf-8")
    git(tmp_path, "add", "staged.py")
    (tmp_path / "untracked.py").write_text("new = True\n", encoding="utf-8")

    service = GitService(tmp_path)
    status = service.status()
    unstaged_diff = service.diff()
    staged_diff = service.diff(staged=True)

    files = {item["path"]: item for item in status["changed_files"]}
    assert status["branch"] == "main"
    assert files["modified.py"]["status"] == "M"
    assert files["staged.py"]["status"] == "A"
    assert files["untracked.py"]["status"] == "U"
    assert "staged.py" in status["staged_files"]
    assert status["untracked_files"] == ["untracked.py"]
    assert "value = 2" in unstaged_diff["diff"]
    assert "staged = True" in staged_diff["diff"]

    git_state = git(tmp_path, "status", "--porcelain=v1", "-z").stdout
    assert git_state


def test_git_service_rejects_non_repository(tmp_path):
    service = GitService(tmp_path)

    try:
        service.status()
    except Exception as error:
        assert str(error) == "Workspace is not a readable Git repository."
    else:
        raise AssertionError("Expected a non-repository error.")
