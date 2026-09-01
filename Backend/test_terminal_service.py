from App.service.Terminal.terminal_service import (
    TerminalService,
    TerminalResult,
)


def test_project_directory_creation(tmp_path):

    service = TerminalService(
        workspace_root=str(tmp_path)
    )

    project_directory = service.get_project_directory(
        project_id=1
    )

    assert project_directory.exists()
    assert project_directory.is_dir()


def test_execute_python_command(tmp_path):

    service = TerminalService(
        workspace_root=str(tmp_path)
    )

    project_directory = service.get_project_directory(
        project_id=1
    )

    main_file = project_directory / "main.py"

    main_file.write_text(
        'print("Hello from terminal")',
        encoding="utf-8"
    )

    result = service.execute_command(
        project_id=1,
        command="python main.py"
    )

    assert isinstance(result, TerminalResult)

    assert result.exit_code == 0
    assert result.success is True
    assert "Hello from terminal" in result.stdout
    assert result.stderr == ""


def test_failed_command(tmp_path):

    service = TerminalService(
        workspace_root=str(tmp_path)
    )

    result = service.execute_command(
        project_id=1,
        command="python -c \"print(undefined_variable)\""
    )

    assert isinstance(result, TerminalResult)

    assert result.exit_code != 0
    assert result.success is False
    assert result.stderr != ""


def test_empty_command(tmp_path):

    service = TerminalService(
        workspace_root=str(tmp_path)
    )

    try:
        service.execute_command(
            project_id=1,
            command=""
        )

        assert False, "Expected ValueError"

    except ValueError as error:

        assert str(error) == "Command cannot be empty."