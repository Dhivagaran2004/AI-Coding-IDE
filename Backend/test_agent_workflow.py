from datetime import datetime
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from App.api import agent_router
from App.auth.auth import get_current_user
from App.database.database import Base, get_db
from App.models.project import Project
from App.models.project_file import ProjectFile
from App.models.user import User
from App.schema.ai_schema import CodeAction
from App.service.AI.agent.agent_task_service import (
    MAX_AGENT_ITERATIONS,
    AgentTaskStore,
    TaskPlanner,
    validate_agent_command,
)
from App.service.AI.agent.agent_tools import AgentToolError, AgentTools
from App.service.AI.code_action_service import CodeActionService


@pytest.fixture
def agent_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        User(id=1, name="Owner", email="agent-owner@example.test", password_hash="x", created_at=datetime.now()),
        User(id=2, name="Other", email="agent-other@example.test", password_hash="x", created_at=datetime.now()),
    ])
    session.flush()
    session.add_all([
        Project(id=1, user_id=1, name="Owned", language="python"),
        Project(id=2, user_id=2, name="Private", language="python"),
    ])
    session.flush()
    session.add_all([
        ProjectFile(
            id=11,
            project_id=1,
            name="main.py",
            type="file",
            content="def calculate():\n    return 1\n",
            language="python",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
        ProjectFile(
            id=12,
            project_id=1,
            name=".env",
            type="file",
            content="API_KEY=long-sensitive-value\n",
            language="dotenv",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
        ProjectFile(
            id=22,
            project_id=2,
            name="private.py",
            type="file",
            content="private_value = True\n",
            language="python",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ),
    ])
    session.commit()
    yield session
    session.close()
    engine.dispose()


def action_payload() -> dict[str, object]:
    return {
        "type": "code_change",
        "operation": "replace",
        "file_path": "main.py",
        "description": "Return the validated value",
        "start_line": 2,
        "end_line": 2,
        "old_code": "    return 1\n",
        "new_code": "    return 2\n",
    }


class FakeAIService:
    def __init__(self, response: str):
        self.responses = response if isinstance(response, list) else [response]

    async def chat(self, message, context=None, history=None):
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class NoopIndexer:
    def index_file(self, file):
        return 0


def make_agent_client(agent_db, monkeypatch, owner_id=1, reset_store=True):
    app = FastAPI()

    async def override_db():
        yield agent_db

    async def override_user():
        return type("UserIdentity", (), {"id": owner_id})()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    monkeypatch.setattr(agent_router, "SessionLocal", lambda: agent_db)
    monkeypatch.setattr(AgentTools, "retrieve_context", lambda self, query: "safe project context")
    monkeypatch.setattr(
        agent_router,
        "CodeActionService",
        lambda db: CodeActionService(db, vector_index_service=NoopIndexer()),
    )
    if reset_store:
        monkeypatch.setattr(agent_router, "agent_tasks", AgentTaskStore())
    app.include_router(agent_router.router)
    return TestClient(app)


def make_plan_response(command=None):
    plan = {
        "plan": ["Inspect the function", "Update the return value"],
        "actions": [action_payload()],
        "validation_command": command,
    }
    return json.dumps(plan)


def test_task_planner_validates_structured_multi_file_actions():
    payload = {"plan": ["Review both files"], "actions": [action_payload(), {
        **action_payload(),
        "file_path": "utils.py",
    }], "validation_command": "python -m pytest"}

    plan, actions, command = TaskPlanner.parse(f"```json\n{json.dumps(payload)}\n```")

    assert plan == ["Review both files"]
    assert len(actions) == 2
    assert command == "python -m pytest"


@pytest.mark.parametrize(
    "command",
    ["rm -rf /", "python -m pytest ../../secrets", "pytest /etc/passwd", "npm install pkg", "git reset --hard", "pytest; whoami", "pytest -p malicious_plugin"],
)
def test_agent_command_validator_rejects_unsafe_commands(command):
    with pytest.raises(ValueError):
        validate_agent_command(command)


def test_agent_command_validator_allows_approved_project_checks():
    assert validate_agent_command("python -m pytest tests/test_auth.py") == "python -m pytest tests/test_auth.py"
    assert validate_agent_command("git diff") == "git diff"


def test_task_store_enforces_owner_and_cancellation():
    store = AgentTaskStore()
    task = store.create(1, 7, "Update validation")

    assert store.get(task.id, 8) is None
    assert store.cancel(task.id, 7).status == "cancelled"
    assert store.get(task.id, 7).stop_reason == "Cancelled by user."
    assert MAX_AGENT_ITERATIONS == 5


def test_agent_tools_block_traversal_secrets_and_other_projects(agent_db):
    tools = AgentTools(agent_db, 1, 1)

    with pytest.raises(AgentToolError):
        tools.inspect_file("../private.py")
    with pytest.raises(AgentToolError):
        tools.inspect_file(".env")
    with pytest.raises(AgentToolError):
        AgentTools(agent_db, 2, 1)
    assert ".env" not in tools.list_project_files()


def test_agent_task_requires_approval_before_applying_changes(agent_db, monkeypatch):
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(make_plan_response()))

    created = client.post("/ai/agent/tasks", json={"project_id": 1, "task": "Update calculate"})
    assert created.status_code == 202
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()
    file = agent_db.get(ProjectFile, 11)
    assert task["status"] == "awaiting_approval"
    assert file.content.endswith("return 1\n")

    applied = client.post(
        f"/ai/agent/tasks/{task['id']}/approve",
        json={"action_indexes": [0]},
    )

    assert applied.status_code == 200
    assert applied.json()["actions"][0]["status"] == "applied"
    assert applied.json()["change_history"][0]["approved_by"] == 1
    assert file.content.endswith("return 2\n")


def test_agent_approval_rejects_stale_patch(agent_db, monkeypatch):
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(make_plan_response()))
    created = client.post("/ai/agent/tasks", json={"project_id": 1, "task": "Update calculate"})
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()
    file = agent_db.get(ProjectFile, 11)
    file.content = "def calculate():\n    return 9\n"
    agent_db.commit()

    response = client.post(
        f"/ai/agent/tasks/{task['id']}/approve",
        json={"action_indexes": [0]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["actions"][0]["status"] == "failed"
    assert file.content.endswith("return 9\n")


def test_agent_task_is_not_visible_to_another_user(agent_db, monkeypatch):
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(make_plan_response()))
    task = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate"},
    ).json()
    other_client = make_agent_client(agent_db, monkeypatch, owner_id=2, reset_store=False)

    response = other_client.get(f"/ai/agent/tasks/{task['id']}")

    assert response.status_code == 404


def test_agent_task_planner_rejects_malformed_output():
    with pytest.raises(ValueError):
        TaskPlanner.parse("not json")
    with pytest.raises(ValueError):
        TaskPlanner.parse(json.dumps({"plan": ["ok"], "actions": [{"operation": "replace"}]}))


def test_task_planner_rejects_multiple_actions_for_one_file():
    with pytest.raises(ValueError, match="one proposed action per file"):
        TaskPlanner.parse(json.dumps({
            "plan": ["Update the function"],
            "actions": [action_payload(), action_payload()],
        }))


def test_rejecting_agent_proposal_leaves_project_file_unchanged(agent_db, monkeypatch):
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(make_plan_response()))
    task = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate"},
    ).json()
    task = client.get(f"/ai/agent/tasks/{task['id']}").json()

    rejected = client.post(
        f"/ai/agent/tasks/{task['id']}/approve",
        json={"reject_all": True},
    )

    assert rejected.status_code == 200
    assert rejected.json()["actions"][0]["status"] == "rejected"
    assert agent_db.get(ProjectFile, 11).content.endswith("return 1\n")


def test_agent_validation_cannot_run_before_change_decision(agent_db, monkeypatch):
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(make_plan_response("python -m pytest")))
    task = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate"},
    ).json()
    task = client.get(f"/ai/agent/tasks/{task['id']}").json()

    response = client.post(
        f"/ai/agent/tasks/{task['id']}/validate",
        json={"command": "python -m pytest", "approved": True},
    )

    assert response.status_code == 409


def test_agent_failure_recovery_requires_approval_and_completes(agent_db, monkeypatch):
    correction = {
        **action_payload(),
        "old_code": "    return 2\n",
        "new_code": "    return 3\n",
        "description": "Correct the return value",
    }
    responses = [
        make_plan_response("python -m pytest"),
        json.dumps({
            "plan": ["Correct the function after failed validation"],
            "actions": [correction],
            "validation_command": "python -m pytest",
        }),
    ]
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(responses))
    terminal_results = iter([
        type("Result", (), {"exit_code": 1, "stdout": "", "stderr": "assertion failed", "success": False})(),
        type("Result", (), {"exit_code": 0, "stdout": "2 passed", "stderr": "", "success": True})(),
    ])
    monkeypatch.setattr(
        agent_router.terminal_service,
        "execute_command",
        lambda project_id, command, timeout_seconds: next(terminal_results),
    )

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate", "context": "CURRENT FILE"},
    )
    task_id = created.json()["id"]
    task = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert task["status"] == "awaiting_approval"

    applied = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"action_indexes": [0]},
    ).json()
    assert applied["status"] == "awaiting_approval"

    failed = client.post(
        f"/ai/agent/tasks/{task_id}/validate",
        json={"command": "python -m pytest", "approved": True},
    ).json()
    assert failed["status"] == "failed"
    assert failed["validation_result"]["stderr"] == "assertion failed"

    continued = client.post(f"/ai/agent/tasks/{task_id}/continue")
    assert continued.status_code == 202
    correction_task = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert correction_task["status"] == "awaiting_approval"
    assert correction_task["iteration"] == 1

    correction_applied = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"action_indexes": [0]},
    ).json()
    assert correction_applied["actions"][0]["status"] == "applied"
    completed = client.post(
        f"/ai/agent/tasks/{task_id}/validate",
        json={"command": "python -m pytest", "approved": True},
    ).json()

    assert completed["status"] == "completed"
    assert completed["validation_result"]["success"] is True
    assert agent_db.get(ProjectFile, 11).content.endswith("return 3\n")