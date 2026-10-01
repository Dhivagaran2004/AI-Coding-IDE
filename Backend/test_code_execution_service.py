from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import subprocess
from threading import Event

import pytest

from App.service.Terminal import code_execution_service as execution_module
from App.service.Terminal.code_execution_service import (
    CodeExecutionResult,
    CodeExecutionService,
    DockerSandboxRunner,
    ExecutionQueueFull,
    ProjectFileNotFound,
    SandboxUnavailable,
    SnapshotFile,
    UnsupportedSourceFile,
)


@dataclass
class FakeFile:
    id: int
    project_id: int
    parent_id: int | None
    name: str
    content: str | None
    type: str = "file"


class FakeRunner:
    def __init__(self):
        self.snapshot = None
        self.entry_path = None

    def run(self, snapshot, entry_path):
        self.snapshot = snapshot
        self.entry_path = entry_path
        return CodeExecutionResult(0, "hello", "", True)


def test_execute_file_snapshots_only_selected_nested_file_and_unsaved_buffer():
    runner = FakeRunner()
    service = CodeExecutionService(runner=runner)
    files = [
        FakeFile(1, 4, None, "src", None, "folder"),
        FakeFile(2, 4, 1, "main.py", "saved version"),
        FakeFile(3, 4, 1, "helper.py", "def helper(): pass"),
        FakeFile(4, 4, None, ".env", "SECRET=value"),
        FakeFile(5, 4, None, "image.png", "binary bytes"),
        FakeFile(6, 9, None, "other.py", "other project"),
    ]

    result = service.execute_file(4, files, 2, "unsaved version")

    assert result.stdout == "hello"
    assert runner.entry_path == "src/main.py"
    assert [item.path for item in runner.snapshot] == ["src/main.py"]
    assert next(
        item.content for item in runner.snapshot if item.path == "src/main.py"
    ) == "unsaved version"
    assert "SECRET" not in runner.snapshot[0].content
    assert all("helper" not in item.content for item in runner.snapshot)


def test_file_from_another_project_is_not_executable():
    service = CodeExecutionService(runner=FakeRunner())

    with pytest.raises(ProjectFileNotFound):
        service.execute_file(
            4,
            [FakeFile(2, 9, None, "main.py", "print('no')")],
            2,
            "print('no')",
        )


@pytest.mark.parametrize(
    "file_name",
    ["notes.txt", ".env", "image.png", "main.pyc"],
)
def test_unsupported_or_sensitive_file_cannot_be_executed(file_name):
    service = CodeExecutionService(runner=FakeRunner())
    project_file = FakeFile(2, 4, None, file_name, "source")

    with pytest.raises(UnsupportedSourceFile):
        service.execute_file(4, [project_file], 2, "source")


def test_invalid_nested_path_is_not_in_snapshot():
    runner = FakeRunner()
    service = CodeExecutionService(runner=runner)
    files = [
        FakeFile(1, 4, None, "..", None, "folder"),
        FakeFile(2, 4, 1, "main.py", "saved"),
        FakeFile(3, 4, None, "safe.py", "print('safe')"),
    ]

    service.execute_file(4, files, 3, "print('safe')")

    assert [item.path for item in runner.snapshot] == ["safe.py"]


def test_empty_source_is_rejected():
    service = CodeExecutionService(runner=FakeRunner())

    with pytest.raises(ValueError, match="empty"):
        service.execute_file(
            4,
            [FakeFile(2, 4, None, "main.py", "saved")],
            2,
            " \n",
        )


def test_worker_queue_is_bounded():
    started = Event()
    release = Event()

    class BlockingRunner:
        def run(self, _snapshot, _entry_path):
            started.set()
            release.wait(timeout=3)
            return CodeExecutionResult(0, "", "", True)

    service = CodeExecutionService(
        runner=BlockingRunner(),
        max_workers=1,
        max_queued_jobs=0,
    )
    files = [FakeFile(2, 4, None, "main.py", "print('ok')")]

    try:
        with ThreadPoolExecutor(max_workers=1) as client_executor:
            first = client_executor.submit(
                service.execute_file,
                4,
                files,
                2,
                "print('first')",
            )
            assert started.wait(timeout=2)
            with pytest.raises(ExecutionQueueFull):
                service.execute_file(4, files, 2, "print('second')")
            release.set()
            assert first.result(timeout=2).success is True
    finally:
        release.set()
        service.executor.shutdown(wait=True)


def test_docker_runner_uses_network_and_resource_isolation(
    monkeypatch,
):
    recorded = {}

    class FakeProcess:
        def __init__(self, command):
            recorded["command"] = command
            self.stdout = BytesIO(b"ok\n")
            self.stderr = BytesIO()

        def wait(self, timeout=None):
            recorded["timeout"] = timeout
            return 0

    def fake_popen(command, **_kwargs):
        recorded["command"] = command
        mount_arg = command[command.index("--mount") + 1]
        source = mount_arg.split("source=", 1)[1].split(",target=", 1)[0]
        assert (Path(source) / "main.py").read_text(encoding="utf-8") == "print('ok')"
        return FakeProcess(command)

    monkeypatch.setattr(execution_module.subprocess, "Popen", fake_popen)
    runner = DockerSandboxRunner()

    result = runner.run(
        [SnapshotFile("main.py", "print('ok')")],
        "main.py",
    )

    command = recorded["command"]
    assert result.success is True
    assert "--network" in command
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert "--user=65534:65534" in command
    assert "--cap-drop=ALL" in command
    assert "--memory=256m" in command
    assert "--pids-limit=64" in command
    assert "python:3.13-alpine" in command
    assert recorded["timeout"] == runner.timeout_seconds


def test_docker_runner_reports_runtime_unavailable(monkeypatch):
    def fake_popen(*_args, **_kwargs):
        raise FileNotFoundError("docker missing")

    monkeypatch.setattr(execution_module.subprocess, "Popen", fake_popen)

    with pytest.raises(SandboxUnavailable):
        DockerSandboxRunner().run(
            [SnapshotFile("main.py", "print('ok')")],
            "main.py",
        )


def test_docker_runner_caps_output_while_draining_pipes(monkeypatch):
    class FakeProcess:
        def __init__(self):
            self.stdout = BytesIO(b"x" * (DockerSandboxRunner.OUTPUT_LIMIT * 3))
            self.stderr = BytesIO()

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(
        execution_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    result = DockerSandboxRunner().run(
        [SnapshotFile("main.py", "print('ok')")],
        "main.py",
    )

    assert len(result.stdout) <= DockerSandboxRunner.OUTPUT_LIMIT + 32
    assert result.stdout.endswith("[output truncated]")


@pytest.mark.parametrize(
    ("extension", "image", "source"),
    [
        (".py", "python:3.13-alpine", "python"),
        (".js", "node:22-alpine", "node"),
        (".ts", "denoland/deno:alpine-2.2.0", "deno"),
        (".tsx", "denoland/deno:alpine-2.2.0", "deno"),
        (".java", "eclipse-temurin:21-jdk-alpine", "javac"),
        (".cpp", "gcc:14", "g++"),
        (".go", "golang:1.24-alpine", "go"),
    ],
)
def test_runner_maps_supported_languages(extension, image, source):
    content = "public class Main {}" if extension == ".java" else "source"
    command = DockerSandboxRunner._language_command(
        extension,
        f"main{extension}",
        [SnapshotFile(f"main{extension}", content)],
    )

    assert DockerSandboxRunner.LANGUAGE_IMAGES[extension] == image
    assert source in " ".join(command)