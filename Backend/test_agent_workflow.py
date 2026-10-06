from datetime import datetime
import asyncio
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
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
        self.loop_ids = []
        self.messages = []
        self.contexts = []

    async def chat(self, message, context=None, history=None):
        self.loop_ids.append(id(asyncio.get_running_loop()))
        self.messages.append(message)
        self.contexts.append(context)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class NoopIndexer:
    def index_file(self, file):
        return 0


class NoopRepositoryIndexer:
    def create_or_update_index(self, file):
        return None


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


def test_task_planner_ignores_unsupported_optional_validation_command():
    payload = {
        "plan": ["Update the login workflow"],
        "actions": [action_payload()],
        "validation_command": "python app.py",
    }

    plan, actions, command = TaskPlanner.parse(json.dumps(payload))

    assert plan == ["Update the login workflow"]
    assert len(actions) == 1
    assert command is None


def test_task_planner_prompt_infers_targets_and_proposes_multifile_changes():
    prompt = TaskPlanner.prompt("Create a login page using HTML and CSS")

    assert "Infer the relevant existing target files" in prompt
    assert "do not require the user to name files" in prompt
    assert "coordinated actions for every necessary file" in prompt
    assert "one separate action per changed file" in prompt
    assert "main/entry-point file" in prompt
    assert "Do not return an empty actions array for an implementation request" in prompt


def test_agent_discovers_import_relationships_to_related_files(agent_db):
    main_file = agent_db.get(ProjectFile, 11)
    main_file.content = "from helpers import calculate\n\ncalculate()\n"
    helper_file = ProjectFile(
        id=14,
        project_id=1,
        name="helpers.py",
        type="file",
        content="def calculate():\n    return 1\n",
        language="python",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    agent_db.add(helper_file)
    agent_db.commit()

    relationships = AgentTools(agent_db, 1, 1).find_file_relationships(["main.py"])

    assert relationships == [("main.py", "helpers.py")]


def test_agent_plans_related_changes_for_main_and_helper_files(agent_db, monkeypatch):
    main_file = agent_db.get(ProjectFile, 11)
    main_file.content = (
        "from helpers import calculate\n\n"
        "def run():\n"
        "    return calculate()\n"
    )
    helper_file = ProjectFile(
        id=14,
        project_id=1,
        name="helpers.py",
        type="file",
        content="def calculate():\n    return 1\n",
        language="python",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    agent_db.add(helper_file)
    agent_db.commit()
    response = json.dumps({
        "plan": ["Update the helper and keep the main entry point connected"],
        "actions": [
            {
                **action_payload(),
                "start_line": 4,
                "end_line": 4,
                "old_code": "    return calculate()\n",
            },
            {
                **action_payload(),
                "file_path": "helpers.py",
                "start_line": 2,
                "end_line": 2,
                "old_code": "    return 1\n",
                "new_code": "    return 2\n",
            },
        ],
        "validation_command": None,
    })
    service = FakeAIService(response)
    monkeypatch.setattr(agent_router, "ai_service", service)
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate and wire it through main.py"},
    )
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()

    assert task["status"] == "awaiting_approval"
    assert len(task["actions"]) == 2
    assert "main.py -> helpers.py" in service.contexts[0]
    assert "FILE: helpers.py\ndef calculate():" in service.contexts[0]


def test_persistent_agent_store_recovers_active_work_as_paused(agent_db):
    persistent_sessions = sessionmaker(bind=agent_db.get_bind())
    first_store = AgentTaskStore(persistent_sessions)
    task = first_store.create(
        project_id=1,
        user_id=1,
        task="Update a validated function",
    )
    task.status = "executing"
    task.actions = [{"status": "applied", "action": action_payload()}]
    task.changes = [{"path": "main.py", "content": "updated", "file_id": 11}]
    task.add_step("code_action", "completed", "Applied approved file change")

    recovered = AgentTaskStore(persistent_sessions).get(task.id, 1)

    assert recovered is not None
    assert recovered.status == "paused"
    assert recovered.actions[0]["status"] == "applied"
    assert recovered.changes[0]["path"] == "main.py"
    assert recovered.steps[-1].description == "Applied approved file change"
    assert AgentTaskStore(persistent_sessions).get(task.id, 2) is None


def prepare_undo_task(agent_db, monkeypatch):
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(
        agent_router,
        "RepositoryIndexService",
        lambda db, project_id: NoopRepositoryIndexer(),
    )
    project_file = agent_db.query(ProjectFile).filter(ProjectFile.id == 11).one()
    previous_content = project_file.content
    current_content = previous_content.replace("return 1", "return 2")
    project_file.content = current_content
    agent_db.commit()

    task = agent_router.agent_tasks.create(1, 1, "Change return value")
    task.status = "completed"
    task.actions = [{"action": action_payload(), "status": "applied", "error": None}]
    task.change_history.append({
        "path": "main.py",
        "file_id": project_file.id,
        "old_hash": hashlib.sha256(previous_content.encode()).hexdigest(),
        "new_hash": hashlib.sha256(current_content.encode()).hexdigest(),
        "previous_content": previous_content,
        "old_code": "    return 1\n",
        "new_code": "    return 2\n",
    })
    task.changes = [{
        "path": "main.py",
        "content": current_content,
        "file_id": project_file.id,
    }]
    return client, task, previous_content


def test_agent_undo_restores_file_when_hash_is_unchanged(agent_db, monkeypatch):
    client, task, previous_content = prepare_undo_task(agent_db, monkeypatch)

    response = client.post(f"/ai/agent/tasks/{task.id}/undo")

    assert response.status_code == 200
    assert response.json()["actions"][0]["status"] == "reverted"
    assert response.json()["change_history"][0]["rolled_back"] is True
    assert agent_db.query(ProjectFile).filter(ProjectFile.id == 11).one().content == previous_content


def test_agent_undo_rejects_a_file_modified_after_ai_change(agent_db, monkeypatch):
    client, task, _ = prepare_undo_task(agent_db, monkeypatch)
    project_file = agent_db.query(ProjectFile).filter(ProjectFile.id == 11).one()
    project_file.content = "user's newer edit\n"
    agent_db.commit()

    response = client.post(f"/ai/agent/tasks/{task.id}/undo")

    assert response.status_code == 409
    assert response.json()["detail"] == "File has changed since this AI action. Review manually."
    assert project_file.content == "user's newer edit\n"


def test_agent_undo_rolls_back_multiple_files_as_one_change_set(agent_db, monkeypatch):
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(
        agent_router,
        "RepositoryIndexService",
        lambda db, project_id: NoopRepositoryIndexer(),
    )
    original_main = agent_db.get(ProjectFile, 11).content
    other_file = ProjectFile(
        id=13,
        project_id=1,
        name="other.py",
        type="file",
        content="value = 2\n",
        language="python",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    agent_db.add(other_file)
    agent_db.flush()
    original_other = "value = 1\n"
    current_main = original_main.replace("return 1", "return 3")
    agent_db.get(ProjectFile, 11).content = current_main
    other_file.content = "value = 2\n"
    agent_db.commit()

    task = agent_router.agent_tasks.create(1, 1, "Update two files")
    task.status = "completed"
    task.actions = [
        {"action": action_payload(), "status": "applied", "error": None},
        {
            "action": {
                **action_payload(),
                "file_path": "other.py",
            },
            "status": "applied",
            "error": None,
        },
    ]
    task.change_history = [
        {
            "path": "main.py",
            "file_id": 11,
            "previous_content": original_main,
            "new_hash": hashlib.sha256(current_main.encode()).hexdigest(),
        },
        {
            "path": "other.py",
            "file_id": 13,
            "previous_content": original_other,
            "new_hash": hashlib.sha256(other_file.content.encode()).hexdigest(),
        },
    ]

    response = client.post(f"/ai/agent/tasks/{task.id}/undo")

    assert response.status_code == 200
    assert agent_db.get(ProjectFile, 11).content == original_main
    assert agent_db.get(ProjectFile, 13).content == original_other
    assert all(change["rolled_back"] for change in response.json()["change_history"])


def test_agent_undo_is_project_and_user_scoped(agent_db, monkeypatch):
    client, task, _ = prepare_undo_task(agent_db, monkeypatch)
    client.app.dependency_overrides[get_current_user] = lambda: type(
        "UserIdentity", (), {"id": 2}
    )()

    response = client.post(f"/ai/agent/tasks/{task.id}/undo")

    assert response.status_code == 404


def test_agent_multi_file_undo_makes_no_partial_changes_on_conflict(
    agent_db,
    monkeypatch,
):
    client, task, original_main = prepare_undo_task(agent_db, monkeypatch)
    other_file = ProjectFile(
        id=13,
        project_id=1,
        name="other.py",
        type="file",
        content="user change\n",
        language="python",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    agent_db.add(other_file)
    agent_db.flush()
    expected_other = "value = 1\n"
    task.change_history.append({
        "path": "other.py",
        "file_id": 13,
        "previous_content": expected_other,
        "new_hash": hashlib.sha256(b"ai change\n").hexdigest(),
    })
    task.change_history[0]["new_hash"] = hashlib.sha256(
        b"ai change\n"
    ).hexdigest()
    task.change_history[0]["file_id"] = 11
    project_file = agent_db.get(ProjectFile, 11)
    project_file.content = "ai change\n"
    agent_db.commit()

    response = client.post(f"/ai/agent/tasks/{task.id}/undo")

    assert response.status_code == 409
    assert project_file.content == "ai change\n"
    assert other_file.content == "user change\n"
    assert task.change_history[0].get("rolled_back") is not True


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


def test_agent_approval_completes_if_repository_reindexing_fails(
    agent_db,
    monkeypatch,
):
    def fail_indexing(self, file):
        raise SQLAlchemyError("Repository index schema is unavailable.")

    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(make_plan_response()))
    monkeypatch.setattr(
        "App.service.AI.code_action_service.RepositoryIndexService.create_or_update_index",
        fail_indexing,
    )
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate"},
    )
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()
    applied = client.post(
        f"/ai/agent/tasks/{task['id']}/approve",
        json={"action_indexes": [0]},
    )

    assert applied.status_code == 200
    assert applied.json()["status"] == "completed"
    assert applied.json()["actions"][0]["status"] == "applied"
    assert applied.json()["changes"][0]["content"].endswith("return 2\n")
    assert agent_db.get(ProjectFile, 11).content.endswith("return 2\n")


def test_agent_retries_empty_plan_with_inferred_file_targets(agent_db, monkeypatch):
    empty_plan = json.dumps({
        "plan": ["Identify the files needed for the login page"],
        "actions": [],
        "validation_command": None,
    })
    service = FakeAIService([empty_plan, make_plan_response()])
    monkeypatch.setattr(agent_router, "ai_service", service)
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Create a login page using HTML and CSS"},
    )
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()

    assert task["status"] == "awaiting_approval"
    assert len(task["actions"]) == 1
    assert "infer the relevant existing target files" in service.messages[1]
    assert any(
        step["description"] == "Retrying implementation plan with relevant files"
        for step in task["steps"]
    )


def test_agent_search_prioritizes_relevant_files_over_common_words(agent_db):
    frontend = ProjectFile(
        id=30,
        project_id=1,
        name="frontend",
        type="folder",
        content=None,
        language=None,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    irrelevant_files = [
        ProjectFile(
            id=file_id,
            project_id=1,
            parent_id=30,
            name=f"component-{file_id}.tsx",
            type="file",
            content="const app = 1;\n",
            language="typescript",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        for file_id in range(31, 37)
    ]
    backend = ProjectFile(
        id=40,
        project_id=1,
        name="backend",
        type="folder",
        content=None,
        language=None,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    auth_file = ProjectFile(
        id=41,
        project_id=1,
        parent_id=40,
        name="auth.py",
        type="file",
        content="def login_user(user):\n    return user\n# python authentication\n",
        language="python",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    agent_db.add_all([frontend, *irrelevant_files, backend, auth_file])
    agent_db.commit()

    results = AgentTools(agent_db, 1, 1).search_project(
        "generate a login auth flow using python"
    )

    assert results[0]["path"] == "backend/auth.py"


def test_agent_fails_and_allows_retry_when_plan_stays_empty(agent_db, monkeypatch):
    empty_plan = json.dumps({
        "plan": ["No file changes were proposed"],
        "actions": [],
        "validation_command": None,
    })
    service = FakeAIService(empty_plan)
    monkeypatch.setattr(agent_router, "ai_service", service)
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Generate a login auth flow using Python"},
    )
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()

    assert task["status"] == "failed"
    assert task["stop_reason"]
    assert len(service.messages) == 3
    assert any(
        step["description"] == "No code changes were proposed"
        for step in task["steps"]
    )


def test_agent_retries_plan_when_replace_action_omits_old_code(agent_db, monkeypatch):
    invalid_action = action_payload()
    invalid_action.pop("old_code")
    invalid_response = json.dumps({
        "plan": ["Update the return value"],
        "actions": [invalid_action],
        "validation_command": None,
    })
    valid_response = make_plan_response()
    monkeypatch.setattr(
        agent_router,
        "ai_service",
        FakeAIService([invalid_response, valid_response]),
    )
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate"},
    )
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()

    assert task["status"] == "awaiting_approval"
    assert task["actions"][0]["action"]["old_code"] == "    return 1\n"
    assert any(step["description"] == "Repairing invalid implementation plan" for step in task["steps"])
    assert [
        step["output"]
        for step in task["steps"]
        if step["type"] == "plan" and step["output"]
    ] == [invalid_response, valid_response]
    assert len(set(agent_router.ai_service.loop_ids)) == 1


def test_agent_repairs_unsupported_create_operation(agent_db, monkeypatch):
    invalid_action = {**action_payload(), "operation": "create"}
    invalid_response = json.dumps({
        "plan": ["Create the requested page"],
        "actions": [invalid_action],
        "validation_command": None,
    })
    service = FakeAIService([invalid_response, make_plan_response()])
    monkeypatch.setattr(agent_router, "ai_service", service)
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Create a login page using HTML and CSS"},
    )
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()

    assert task["status"] == "awaiting_approval"
    assert task["actions"][0]["action"]["operation"] == "replace"
    assert "operation=replace exactly" in service.messages[0]
    assert "do not use create, create_file, insert, or delete" in service.messages[1]
    assert any(
        step["description"] == "Repairing invalid implementation plan"
        for step in task["steps"]
    )


def test_agent_retries_invalid_plan_and_can_retry_after_repair_limit(agent_db, monkeypatch):
    invalid_response = json.dumps({
        "plan": ["Update the return value"],
        "actions": [{
            **action_payload(),
            "old_code": "",
        }],
        "validation_command": None,
    })
    service = FakeAIService([
        invalid_response,
        invalid_response,
        invalid_response,
        make_plan_response(),
    ])
    monkeypatch.setattr(agent_router, "ai_service", service)
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate"},
    )
    task_id = created.json()["id"]
    failed = client.get(f"/ai/agent/tasks/{task_id}").json()

    assert failed["status"] == "failed"
    assert len(service.messages) == 3
    assert sum(
        step["description"] == "Repairing invalid implementation plan"
        for step in failed["steps"]
    ) == 2
    assert "empty old_code is only valid at line 1" in service.messages[1]
    assert "empty old_code is only valid at line 1" in service.messages[2]

    retried = client.post(f"/ai/agent/tasks/{task_id}/continue")
    recovered = client.get(f"/ai/agent/tasks/{task_id}").json()

    assert retried.status_code == 202
    assert recovered["status"] == "awaiting_approval"
    assert recovered["iteration"] == 1
    assert recovered["actions"][0]["status"] == "awaiting_approval"
    assert len(service.messages) == 4


def test_agent_can_fill_an_empty_existing_file(agent_db, monkeypatch):
    file = agent_db.get(ProjectFile, 11)
    file.content = ""
    agent_db.commit()
    new_content = "<!DOCTYPE html>\n<html><body>Hello</body></html>\n"
    action = {
        **action_payload(),
        "start_line": 1,
        "end_line": 1,
        "old_code": "",
        "new_code": new_content,
    }
    response = json.dumps({
        "plan": ["Add the website markup"],
        "actions": [action],
        "validation_command": None,
    })
    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(response))
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Create a website"},
    )
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()
    applied = client.post(
        f"/ai/agent/tasks/{task['id']}/approve",
        json={"action_indexes": [0]},
    )

    assert task["status"] == "awaiting_approval"
    assert applied.status_code == 200
    assert applied.json()["actions"][0]["status"] == "applied", applied.json()
    assert applied.json()["changes"][0]["content"] == new_content
    file = agent_db.get(ProjectFile, 11)
    assert file.content == new_content


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


def test_agent_can_approve_remaining_actions_after_stale_patch(agent_db, monkeypatch):
    response = json.dumps({
        "plan": ["Update both files"],
        "actions": [
            action_payload(),
            {
                **action_payload(),
                "file_path": "other.py",
                "start_line": 1,
                "end_line": 1,
                "old_code": "value = 1\n",
                "new_code": "value = 2\n",
            },
        ],
        "validation_command": None,
    })
    other_file = ProjectFile(
        id=13,
        project_id=1,
        name="other.py",
        type="file",
        content="value = 1\n",
        language="python",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    agent_db.add(other_file)
    agent_db.commit()
    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(response))
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post("/ai/agent/tasks", json={"project_id": 1, "task": "Update both files"})
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()
    stale_file = agent_db.get(ProjectFile, 11)
    stale_file.content = "def calculate():\n    return 9\n"
    agent_db.commit()

    stale_approval = client.post(
        f"/ai/agent/tasks/{task['id']}/approve",
        json={"action_indexes": [0]},
    )
    remaining_approval = client.post(
        f"/ai/agent/tasks/{task['id']}/approve",
        json={"action_indexes": [1]},
    )

    assert stale_approval.status_code == 200
    assert stale_approval.json()["status"] == "awaiting_approval"
    assert stale_approval.json()["actions"][0]["status"] == "failed"
    assert stale_approval.json()["actions"][1]["status"] == "awaiting_approval"
    assert remaining_approval.status_code == 200
    assert remaining_approval.json()["status"] == "failed"
    assert remaining_approval.json()["actions"][1]["status"] == "applied"
    assert remaining_approval.json()["changes"][0]["content"] == "value = 2\n"
    assert stale_file.content.endswith("return 9\n")
    saved_other_file = agent_db.query(ProjectFile).filter(ProjectFile.id == 13).one()
    assert saved_other_file.content == "value = 2\n"


def test_agent_approval_preserves_file_changes_outside_patch(agent_db, monkeypatch):
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(make_plan_response()))
    created = client.post("/ai/agent/tasks", json={"project_id": 1, "task": "Update calculate"})
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()
    file = agent_db.get(ProjectFile, 11)
    file.content += "# user edit after proposal\n"
    agent_db.commit()

    response = client.post(
        f"/ai/agent/tasks/{task['id']}/approve",
        json={"action_indexes": [0]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["actions"][0]["status"] == "applied"
    assert file.content == "def calculate():\n    return 2\n# user edit after proposal\n"


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