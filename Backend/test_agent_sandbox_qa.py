import asyncio
import json
from types import SimpleNamespace

import pytest

from App.schema.ai_schema import CodeAction
from App.schema.agent_schema import AgentPlan
from App.api import agent_router
from App.service.AI.agent.agent_task_service import AgentTask
from App.service.AI.agent.agent_sandbox_qa import (
    AgentSandboxQAError,
    build_sandbox_qa_command,
    build_sandbox_snapshot,
    run_sandbox_validation,
    select_validation_command,
)
from App.service.sandbox_service import SandboxExecutionResult


class SnapshotTools:
    def __init__(self, files: dict[str, str]):
        self.files = files

    def list_project_files(self) -> list[str]:
        return list(self.files)

    def inspect_file(self, path: str) -> dict[str, object]:
        return {"content": self.files[path]}

    def resolve_file(self, path: str):
        return SimpleNamespace(content=self.files[path])


def create_action(path: str, code: str) -> CodeAction:
    return CodeAction(
        type="code_change",
        operation="create_file",
        file_path=path,
        description=f"Create {path}",
        new_code=code,
    )


def replace_action(path: str, old_code: str, new_code: str) -> CodeAction:
    return CodeAction(
        type="code_change",
        operation="replace",
        file_path=path,
        description=f"Update {path}",
        start_line=1,
        end_line=1,
        old_code=old_code,
        new_code=new_code,
    )


def test_sandbox_snapshot_stages_create_and_replace_without_mutating_sources():
    source_files = {"main.py": "return_value = 1\n"}
    tools = SnapshotTools(source_files)

    snapshot = build_sandbox_snapshot(
        tools,
        [
            replace_action("main.py", "return_value = 1\n", "return_value = 2\n"),
            create_action("tests/test_main.py", "def test_main():\n    assert True\n"),
        ],
    )

    assert snapshot["main.py"] == "return_value = 2\n"
    assert "tests/test_main.py" in snapshot
    assert source_files["main.py"] == "return_value = 1\n"


def test_sandbox_snapshot_rejects_protected_paths_and_stale_patches():
    tools = SnapshotTools({"main.py": "return_value = 1\n"})

    with pytest.raises(AgentSandboxQAError, match="protected"):
        build_sandbox_snapshot(tools, [create_action(".env", "TOKEN=x")])
    with pytest.raises(AgentSandboxQAError, match="does not match"):
        build_sandbox_snapshot(
            tools,
            [replace_action("main.py", "return_value = 0\n", "return_value = 2\n")],
        )


def test_validation_command_is_selected_from_project_tests_and_manifests():
    assert select_validation_command(
        {"tests/test_main.py": "def test_main(): pass\n"},
        None,
    ) == "python -m pytest"
    assert select_validation_command(
        {"main.py": "print('ok')\n"},
        None,
    ) == "python -m compileall ."
    assert select_validation_command(
        {"package.json": '{"scripts":{"build":"vite build"}}'},
        None,
    ) == "npm run build"
    assert select_validation_command({}, None) is None
    assert select_validation_command({}, "python main.py") == "python main.py"


def test_sandbox_installs_declared_dependencies_only_from_offline_caches():
    python_command = build_sandbox_qa_command(
        {
            "requirements.txt": "pytest\n",
            "tests/test_main.py": "def test_main(): pass\n",
        },
        "python -m pytest",
    )
    assert "--no-index --find-links=/opt/wheelhouse" in python_command
    assert python_command.endswith("python -m pytest")

    node_command = build_sandbox_qa_command(
        {
            "package.json": '{"scripts":{"test":"vitest"}}',
            "package-lock.json": "{}",
        },
        "npm test",
    )
    assert "npm ci --offline --cache=/opt/npm-cache" in node_command
    assert node_command.endswith("npm test")


def test_sandbox_validation_preserves_execution_result():
    class FakeSandbox:
        def execute_snapshot(self, **kwargs):
            assert kwargs["files"] == {"main.py": "print('ok')\n"}
            assert kwargs["command"] == "python main.py"
            return SandboxExecutionResult(
                exit_code=0,
                stdout="ok\n",
                stderr="",
                success=True,
            )

    result = run_sandbox_validation(
        sandbox=FakeSandbox(),  # type: ignore[arg-type]
        project_id=1,
        files={"main.py": "print('ok')\n"},
        command="python main.py",
    )

    assert result.executed
    assert result.success
    assert result.stdout == "ok\n"


def test_sandbox_snapshot_rejects_duplicate_or_existing_creates():
    tools = SnapshotTools({"main.py": "print('ok')\n"})

    with pytest.raises(AgentSandboxQAError, match="already exists"):
        build_sandbox_snapshot(tools, [create_action("main.py", "print('new')\n")])


def test_sandbox_qa_diagnoses_and_retries_a_failed_change(monkeypatch):
    task = AgentTask(
        id="task-qa",
        project_id=1,
        user_id=1,
        task="Create a working script.",
    )
    tools = SnapshotTools({"main.py": "print('old')\n"})
    plan = AgentPlan.model_validate({
        "plan_id": "qa-plan",
        "objective": "Create a working script.",
        "project_type": "Python application",
        "scope": "A single Python script.",
        "project_understanding": "The project has one Python entry point.",
        "assumptions": [],
        "requirements": [{
            "id": "req-script",
            "description": "The script runs successfully.",
            "category": "functional",
        }],
        "acceptance_criteria": ["The script runs successfully."],
        "technical_approach": "Use Python.",
        "tasks": [{
            "id": "task-script",
            "task_type": "implementation",
            "description": "Implement the script.",
            "objective": "Make the script run.",
            "dependencies": [],
            "requirement_ids": ["req-script"],
            "expected_files": ["main.py"],
            "required_tools": ["update_file"],
            "acceptance_criteria": ["The script exits successfully."],
            "verification_steps": ["Run the script."],
            "status": "pending",
        }],
        "risks": [],
        "validation_strategy": ["Run the script."],
    })
    initial_action = replace_action(
        "main.py",
        "print('old')\n",
        "raise SystemExit(1)\n",
    )
    correction_action = replace_action(
        "main.py",
        "print('old')\n",
        "print('fixed')\n",
    )
    correction = {
        "status": "ready",
        "plan": plan.model_dump(mode="json"),
        "clarification_questions": [],
        "actions": [correction_action.model_dump()],
        "validation_command": "python main.py",
    }

    calls = []

    async def fake_chat_json(**kwargs):
        calls.append(kwargs["message"])
        if len(calls) == 1:
            return json.dumps({
                "issues": ["The script exits with status 1."],
                "root_causes": ["The proposed source explicitly raises an error."],
                "fix_plan": ["Remove the failing exit and rerun the script."],
            })
        return json.dumps(correction)

    runs = []

    def fake_validation(**kwargs):
        runs.append(kwargs["files"]["main.py"])
        succeeded = len(runs) == 2
        return agent_router.TerminalContext(
            command=kwargs["command"],
            exit_code=0 if succeeded else 1,
            stdout="fixed\n" if succeeded else "",
            stderr="" if succeeded else "exit status 1",
            success=succeeded,
        )

    monkeypatch.setattr(agent_router.ai_service, "chat_json", fake_chat_json)
    monkeypatch.setattr(agent_router.sandbox_service, "is_available", lambda: True)
    monkeypatch.setattr(agent_router, "run_sandbox_validation", fake_validation)

    updated_plan, updated_actions, command = asyncio.run(
        agent_router._run_sandbox_plan_qa(
            task=task,
            tools=tools,  # type: ignore[arg-type]
            context="A safe Python project snapshot.",
            response="Initial structured plan.",
            plan_details=plan,
            actions=[initial_action],
            command="python main.py",
        )
    )

    assert updated_plan.plan_id == "qa-plan"
    assert updated_actions[0].new_code == "print('fixed')\n"
    assert command == "python main.py"
    assert runs == ["raise SystemExit(1)\n", "print('fixed')\n"]
    assert any(step.type == "diagnosis" for step in task.steps)
