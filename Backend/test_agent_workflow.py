from datetime import datetime
import asyncio
import hashlib
import json
from types import SimpleNamespace

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
from App.service.AI.agent import agent_tools as agent_tools_module
from App.service.AI.agent.agent_tools import AgentToolError, AgentTools
from App.service.AI.code_action_service import CodeActionService, PatchNotFoundError


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


@pytest.fixture(autouse=True)
def disable_implicit_docker_runs_in_unit_tests(monkeypatch):
    monkeypatch.setattr(agent_router.sandbox_service, "is_available", lambda: False)


@pytest.fixture(autouse=True)
def isolate_planning_tests_from_docker(monkeypatch):
    async def skip_sandbox_qa(
        task,
        tools,
        context,
        response,
        plan_details,
        actions,
        command,
    ):
        return plan_details, actions, command

    monkeypatch.setattr(agent_router, "_run_sandbox_plan_qa", skip_sandbox_qa)


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


def adapt_legacy_test_plan(response: str) -> str:
    try:
        payload = json.loads(response)
    except (ValueError, TypeError):
        return response
    if not isinstance(payload, dict) or not isinstance(payload.get("plan"), list):
        return response

    plan_items = [
        item.strip()
        for item in payload["plan"]
        if isinstance(item, str) and item.strip()
    ] or ["Review the requested project task"]
    actions = payload.get("actions", [])
    action_paths = [
        item["file_path"]
        for item in actions
        if isinstance(item, dict) and isinstance(item.get("file_path"), str)
    ] if isinstance(actions, list) else []
    task_type = "implementation" if action_paths else "analysis"
    requirement_category = "functional" if action_paths else "analysis"
    tasks = []
    for index, description in enumerate(plan_items):
        current_type = task_type if index == 0 else (
            "verification" if action_paths else "analysis"
        )
        tasks.append({
            "id": f"step-{index + 1}",
            "task_type": current_type,
            "description": description,
            "objective": description,
            "dependencies": [f"step-{index}"] if index else [],
            "requirement_ids": ["req-1"],
            "expected_files": action_paths if index == 0 and action_paths else [],
            "required_tools": [],
            "acceptance_criteria": [description],
            "verification_steps": [
                payload.get("validation_command") or "Review the result against the request."
            ],
            "status": "pending",
        })
    payload["plan"] = {
        "plan_id": "test-plan",
        "objective": plan_items[0],
        "project_type": "existing project",
        "scope": plan_items[0],
        "project_understanding": "The project context is represented by the test fixture.",
        "assumptions": [],
        "requirements": [{
            "id": "req-1",
            "description": plan_items[0],
            "category": requirement_category,
        }],
        "acceptance_criteria": [plan_items[0]],
        "technical_approach": "Use the existing project structure.",
        "tasks": tasks,
        "risks": [],
        "validation_strategy": [
            payload.get("validation_command") or "Review the result against the request."
        ],
    }
    payload["status"] = "ready"
    payload["clarification_questions"] = []
    return json.dumps(payload)


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
            response = self.responses.pop(0)
        else:
            response = self.responses[0]
        return adapt_legacy_test_plan(response)

    async def chat_json(self, message, context=None, history=None):
        return await self.chat(message, context=context, history=history)


class NoopIndexer:
    def index_file(self, file):
        return 0


class NoopRepositoryIndexer:
    def create_or_update_index(self, file):
        return None


def make_agent_client(
    agent_db,
    monkeypatch,
    owner_id=1,
    reset_store=True,
    sync_workspace=False,
):
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
    if not sync_workspace:
        monkeypatch.setattr(
            agent_router.ProjectWorkspaceService,
            "sync_file",
            lambda *_args, **_kwargs: None,
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


def make_structured_plan_response(command=None, actions=None):
    return adapt_legacy_test_plan(json.dumps({
        "plan": ["Implement the requested behavior", "Verify the result"],
        "actions": [action_payload()] if actions is None else actions,
        "validation_command": command,
    }))


def make_dependency_plan_response(command=None):
    payload = json.loads(make_structured_plan_response(command))
    template = payload["plan"]["tasks"][0]
    payload["actions"] = [
        action_payload(),
        {
            "type": "code_change",
            "operation": "create_file",
            "file_path": "helper.py",
            "description": "Create a helper module",
            "new_code": "def helper():\n    return True\n",
        },
    ]
    payload["plan"]["tasks"] = [
        {
            **template,
            "id": "step-1",
            "description": "Update the entry point",
            "objective": "Update the entry point",
            "expected_files": ["main.py"],
        },
        {
            **template,
            "id": "step-2",
            "description": "Create the supporting helper",
            "objective": "Create the supporting helper",
            "dependencies": ["step-1"],
            "expected_files": ["helper.py"],
        },
        {
            **template,
            "id": "step-3",
            "task_type": "verification",
            "description": "Verify the application",
            "objective": "Verify the application",
            "dependencies": ["step-2"],
            "expected_files": [],
        },
    ]
    return json.dumps(payload)


def acceptance_results_for(
    task: dict[str, object],
    plan_task_id: str,
    evidence: str,
    status: str = "met",
) -> list[dict[str, object]]:
    plan_details = task["plan_details"]
    assert isinstance(plan_details, dict)
    tasks = plan_details["tasks"]
    assert isinstance(tasks, list)
    plan_task = next(
        row for row in tasks
        if isinstance(row, dict) and row.get("id") == plan_task_id
    )
    criteria = plan_task["acceptance_criteria"]
    assert isinstance(criteria, list)
    return [
        {
            "criterion": str(criterion),
            "status": status,
            "evidence": evidence if status != "not_verified" else None,
        }
        for criterion in criteria
    ]


def test_task_planner_validates_structured_multi_file_actions():
    payload = {"plan": ["Review both files"], "actions": [action_payload(), {
        **action_payload(),
        "file_path": "utils.py",
    }], "validation_command": "python -m pytest"}

    plan, actions, command = TaskPlanner.parse(f"```json\n{json.dumps(payload)}\n```")

    assert plan == ["Review both files"]
    assert len(actions) == 2
    assert command == "python -m pytest"


def test_task_planner_skips_valid_non_object_json_before_fenced_plan():
    payload = {
        "plan": ["Update the function"],
        "actions": [action_payload()],
        "validation_command": None,
    }

    plan, actions, command = TaskPlanner.parse(
        f"null\n```json\n{json.dumps(payload)}\n```"
    )

    assert plan == ["Update the function"]
    assert len(actions) == 1
    assert command is None


def test_task_planner_accepts_project_local_python_validation_command():
    payload = {
        "plan": ["Update the login workflow"],
        "actions": [action_payload()],
        "validation_command": "python app.py",
    }

    plan, actions, command = TaskPlanner.parse(json.dumps(payload))

    assert plan == ["Update the login workflow"]
    assert len(actions) == 1
    assert command == "python app.py"


def test_structured_planner_checks_plan_action_file_coverage():
    response = make_structured_plan_response()
    result = TaskPlanner.parse_planning_result(response)

    assert result.status == "ready"
    assert result.plan is not None
    assert result.plan.requirements[0].category == "functional"
    assert result.plan.tasks[0].expected_files == ["main.py"]
    assert result.actions[0].file_path == "main.py"

    payload = json.loads(response)
    payload["plan"]["tasks"][0]["expected_files"] = ["main.py", "missing.py"]
    with pytest.raises(ValueError, match="missing actions for expected files"):
        TaskPlanner.parse_planning_result(json.dumps(payload))


def test_structured_planner_normalizes_singleton_text_lists_and_missing_plan_id():
    payload = json.loads(make_structured_plan_response())
    payload["plan"].pop("plan_id")
    payload["plan"]["validation_strategy"] = "Run the backend tests."
    payload["plan"]["tasks"][0]["acceptance_criteria"] = (
        "Employee records can be created."
    )

    result = TaskPlanner.parse_planning_result(json.dumps(payload))

    assert result.plan is not None
    assert result.plan.plan_id.startswith("plan-")
    assert result.plan.validation_strategy == ["Run the backend tests."]
    assert result.plan.tasks[0].acceptance_criteria == [
        "Employee records can be created."
    ]


def test_structured_planner_accepts_verification_requirements_and_structured_risks():
    payload = json.loads(make_structured_plan_response())
    payload.pop("validation_command")
    payload["plan"]["requirements"].append({
        "id": "req-quality",
        "description": "Exercise the API with automated tests.",
        "category": "quality",
    })
    payload["plan"]["tasks"][1]["requirement_ids"].append("req-quality")
    payload["plan"]["risks"] = [{
        "risk": "Database schema changes may break existing data.",
        "mitigation": "Use isolated test databases.",
    }]

    result = TaskPlanner.parse_planning_result(json.dumps(payload))

    assert result.validation_command is None
    assert result.plan is not None
    assert result.plan.risks == [
        "Database schema changes may break existing data. "
        "(mitigation: Use isolated test databases.)"
    ]


def test_structured_planner_accepts_more_than_ten_actions_with_file_ownership():
    payload = json.loads(make_structured_plan_response())
    paths = [f"module_{index}.py" for index in range(11)]
    actions = []
    for path in paths:
        action = action_payload()
        action["file_path"] = path
        actions.append(action)

    first_task = payload["plan"]["tasks"][0]
    first_task["expected_files"] = paths[:6]
    second_task = {
        **first_task,
        "id": "step-3",
        "description": "Create supporting modules",
        "objective": "Create supporting modules",
        "dependencies": ["step-1"],
        "expected_files": paths[6:],
    }
    payload["plan"]["tasks"].append(second_task)
    payload["actions"] = actions

    result = TaskPlanner.parse_planning_result(json.dumps(payload))

    assert len(result.actions) == 11
    assert {
        path
        for task in result.plan.tasks
        for path in task.expected_files
    } == set(paths)


def test_structured_planner_reports_action_count_and_limit():
    payload = json.loads(make_structured_plan_response())
    payload["actions"] = [action_payload() for _ in range(41)]

    with pytest.raises(ValueError, match="41 actions; the current limit is 40"):
        TaskPlanner.parse_planning_result(json.dumps(payload))


def test_structured_planner_rejects_empty_actions_and_disallowed_commands():
    payload = json.loads(make_structured_plan_response())
    del payload["actions"]
    with pytest.raises(ValueError, match="must include an actions array"):
        TaskPlanner.parse_planning_result(json.dumps(payload))

    payload = json.loads(make_structured_plan_response())
    payload["actions"] = [{}]
    with pytest.raises(ValueError):
        TaskPlanner.parse_planning_result(json.dumps(payload))

    payload = json.loads(make_structured_plan_response())
    payload["actions"][0]["operation"] = "delete"
    with pytest.raises(ValueError, match="Only validated replace and create_file"):
        TaskPlanner.parse_planning_result(json.dumps(payload))

    payload = json.loads(make_structured_plan_response())
    payload["validation_command"] = "pytest; whoami"
    with pytest.raises(ValueError, match="disallowed validation command"):
        TaskPlanner.parse_planning_result(json.dumps(payload))

    payload = json.loads(make_structured_plan_response())
    payload["plan"]["tasks"][0]["verification_status"] = "verified"
    payload["plan"]["tasks"][0]["acceptance_results"] = [{
        "criterion": payload["plan"]["tasks"][0]["acceptance_criteria"][0],
        "status": "met",
        "evidence": "Model asserted the criterion passed.",
    }]
    with pytest.raises(ValueError, match="pending status"):
        TaskPlanner.parse_planning_result(json.dumps(payload))


def test_structured_planner_rejects_duplicate_ids_cycles_and_missing_verification():
    payload = json.loads(make_structured_plan_response())
    first_task = payload["plan"]["tasks"][0]
    second_task = {
        **first_task,
        "id": "step-3",
        "task_type": "verification",
        "description": "Verify the implementation",
        "objective": "Verify the implementation",
        "expected_files": [],
        "dependencies": ["step-1"],
    }
    first_task["dependencies"] = ["step-3"]
    payload["plan"]["tasks"].append(second_task)
    with pytest.raises(ValueError, match="cycle"):
        TaskPlanner.parse_planning_result(json.dumps(payload))

    payload = json.loads(make_structured_plan_response())
    payload["plan"]["tasks"][0]["verification_steps"] = []
    with pytest.raises(ValueError):
        TaskPlanner.parse_planning_result(json.dumps(payload))

    payload = json.loads(make_structured_plan_response())
    payload["plan"]["tasks"][0]["dependencies"] = ["missing-task"]
    with pytest.raises(ValueError, match="unknown dependencies"):
        TaskPlanner.parse_planning_result(json.dumps(payload))

    payload = json.loads(make_structured_plan_response())
    payload["plan"]["tasks"][0]["requirement_ids"] = ["missing-requirement"]
    with pytest.raises(ValueError, match="unknown requirements"):
        TaskPlanner.parse_planning_result(json.dumps(payload))

    payload = json.loads(make_structured_plan_response())
    payload["plan"]["requirements"].append({
        **payload["plan"]["requirements"][0],
        "description": "Duplicate requirement",
    })
    with pytest.raises(ValueError, match="requirement IDs must be unique"):
        TaskPlanner.parse_planning_result(json.dumps(payload))

    payload = json.loads(make_structured_plan_response())
    payload["plan"]["tasks"][1]["id"] = payload["plan"]["tasks"][0]["id"]
    with pytest.raises(ValueError, match="task IDs must be unique"):
        TaskPlanner.parse_planning_result(json.dumps(payload))


def test_task_planner_prompt_infers_targets_and_proposes_multifile_changes():
    prompt = TaskPlanner.prompt(TaskPlanner.include_clarifications(
        "Create a full-stack employee system",
        [{
            "question": "Which phase should come first?",
            "answer": "Start with the backend, then do frontend phases.",
        }],
    ))

    assert "Infer the relevant existing target files" in prompt
    assert "do not require the user to name files" in prompt
    assert "coordinated actions for every necessary file" in prompt
    assert "one separate action per changed file" in prompt
    assert "main/entry-point file" in prompt
    assert "Do not return an empty actions array for an implementation request" in prompt
    assert "Never ask a clarification question solely because of the 40-action" in prompt
    assert "Start with the backend, then do frontend phases." in prompt


def test_task_planner_detects_only_action_limit_clarifications():
    assert TaskPlanner.is_action_limit_clarification([
        "The maximum action limit is 40; should I split this into phases?"
    ])
    assert not TaskPlanner.is_action_limit_clarification([
        "Which database should the application use?"
    ])
    assert not TaskPlanner.is_action_limit_clarification([
        "The action limit is 40.",
        "Should employee salaries be visible to all users?",
    ])


def test_task_planner_accepts_create_file_content_payload_without_losing_contents():
    employee_source = "class Employee:\n    pass\n"
    main_source = "from employee import Employee\n"
    payload = {
        "plan": ["Create the employee model and application entry point."],
        "actions": [
            {
                "type": "code_change",
                "operation": "create_file",
                "file_path": "employee.py",
                "description": "Define the employee model",
                "content": employee_source,
            },
            {
                "type": "create_file",
                "file_path": "main.py",
                "description": "Create the application entry point",
                "content": main_source,
            },
        ],
        "validation_command": "python main.py",
    }

    plan, actions, command = TaskPlanner.parse(json.dumps(payload))

    assert plan == ["Create the employee model and application entry point."]
    assert [action.operation for action in actions] == [
        "create_file",
        "create_file",
    ]
    assert [action.new_code for action in actions] == [
        employee_source,
        main_source,
    ]
    assert command == "python main.py"


def test_task_planner_rejects_conflicting_create_file_content_fields():
    payload = {
        "plan": ["Create a module."],
        "actions": [{
            "type": "code_change",
            "operation": "create_file",
            "file_path": "employee.py",
            "description": "Create employee model",
            "content": "class Employee: pass\n",
            "new_code": "class Other: pass\n",
        }],
        "validation_command": None,
    }

    with pytest.raises(ValueError, match="conflicting content and new_code"):
        TaskPlanner.parse(json.dumps(payload))


def test_repair_prompt_allows_creating_files_when_project_is_empty():
    prompt = TaskPlanner.repair_prompt(
        "Create an employee management system",
        '{"plan":[]}',
        "The plan omitted required actions.",
    )

    assert "operation=create_file with new_code" in prompt
    assert "do not use a top-level type=create_file" in prompt
    assert "Modify existing files only" not in prompt


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


def test_agent_stages_dependent_actions_until_prerequisite_is_verified(
    agent_db,
    monkeypatch,
):
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(
        agent_router,
        "ai_service",
        FakeAIService(make_dependency_plan_response()),
    )
    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update the application and add a helper"},
    ).json()
    task_id = created["id"]
    task = client.get(f"/ai/agent/tasks/{task_id}").json()

    assert task["status"] == "awaiting_approval"
    assert [item["status"] for item in task["actions"]] == [
        "awaiting_approval",
        "scheduled",
    ]
    assert [item["status"] for item in task["plan_details"]["tasks"]] == [
        "in_progress",
        "pending",
        "pending",
    ]
    blank_notes = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={"plan_task_id": "step-1", "verification_notes": "  "},
    )
    assert blank_notes.status_code == 422
    premature = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-2",
            "verification_notes": "The helper is ready.",
        },
    )
    assert premature.status_code == 409

    applied_first = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"action_indexes": [0]},
    ).json()
    assert applied_first["status"] == "awaiting_validation"
    assert applied_first["actions"][0]["status"] == "applied"
    assert applied_first["actions"][1]["status"] == "scheduled"
    assert applied_first["plan_details"]["tasks"][0]["status"] == "applied"

    not_verified = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-1",
            "verification_notes": "The entry point behavior is correct.",
        },
    ).json()
    assert not_verified["status"] == "awaiting_validation"
    assert not_verified["verification_status"] == "not_verified"
    assert not_verified["plan_details"]["tasks"][0]["status"] == "applied"
    assert not_verified["plan_details"]["tasks"][0]["verification_status"] == "not_verified"

    completed_first = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-1",
            "verification_notes": "The entry point behavior is correct.",
            "acceptance_results": acceptance_results_for(
                not_verified,
                "step-1",
                "Confirmed the entry point returns the specified behavior.",
            ),
        },
    ).json()
    assert completed_first["status"] == "awaiting_approval"
    assert completed_first["actions"][1]["status"] == "awaiting_approval"
    assert completed_first["plan_details"]["tasks"][1]["status"] == "in_progress"
    assert completed_first["plan_details"]["tasks"][0]["verification_status"] == "verified"

    applied_second = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"action_indexes": [1]},
    ).json()
    assert applied_second["status"] == "awaiting_validation"
    assert applied_second["plan_details"]["tasks"][1]["status"] == "applied"

    completed_second = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-2",
            "verification_notes": "The helper returns the expected value.",
            "acceptance_results": acceptance_results_for(
                applied_second,
                "step-2",
                "Called the helper and confirmed its expected return value.",
            ),
        },
    ).json()
    assert completed_second["status"] == "awaiting_validation"
    assert completed_second["plan_details"]["tasks"][2]["status"] == "in_progress"

    completed_verification = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-3",
            "verification_notes": "The application and helper are consistent.",
            "acceptance_results": acceptance_results_for(
                completed_second,
                "step-3",
                "Confirmed the application and helper work together.",
            ),
        },
    ).json()
    assert completed_verification["status"] == "completed"
    assert completed_verification["verification_status"] == "verified"
    assert [item["status"] for item in completed_verification["plan_details"]["tasks"]] == [
        "completed",
        "completed",
        "completed",
    ]
    assert any(
        entry["plan_task_id"] == "step-1"
        and entry.get("verification_notes") == "The entry point behavior is correct."
        for entry in completed_verification["task_history"]
    )


def test_rejected_plan_action_blocks_dependent_tasks(agent_db, monkeypatch):
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(
        agent_router,
        "ai_service",
        FakeAIService(make_dependency_plan_response()),
    )
    task = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update the application and add a helper"},
    ).json()

    rejected = client.post(
        f"/ai/agent/tasks/{task['id']}/approve",
        json={"reject_all": True},
    ).json()

    assert rejected["status"] == "failed"
    assert [item["status"] for item in rejected["actions"]] == [
        "rejected",
        "blocked",
    ]
    assert [item["status"] for item in rejected["plan_details"]["tasks"]] == [
        "blocked",
        "blocked",
        "blocked",
    ]


def test_successful_validation_completes_verification_task(agent_db, monkeypatch):
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(
        agent_router,
        "ai_service",
        FakeAIService(make_structured_plan_response("python -m pytest")),
    )
    monkeypatch.setattr(
        agent_router,
        "_run_project_validation_command",
        lambda **kwargs: (
            agent_router.TerminalContext(
                command=kwargs["command"],
                exit_code=0,
                stdout="2 passed",
                stderr="",
                success=True,
                timeout=False,
            ),
            [],
        ),
    )
    task = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate and test it"},
    ).json()
    task_id = task["id"]

    applied = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"accept_all": True},
    ).json()
    assert applied["status"] == "awaiting_validation"
    assert applied["plan_details"]["tasks"][0]["status"] == "applied"

    ready_for_validation = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-1",
            "verification_notes": "The requested behavior is implemented.",
            "acceptance_results": acceptance_results_for(
                applied,
                "step-1",
                "Confirmed calculate returns the requested value.",
            ),
        },
    ).json()
    assert ready_for_validation["status"] == "awaiting_approval"
    assert ready_for_validation["plan_details"]["tasks"][1]["status"] == "in_progress"

    validated = client.post(
        f"/ai/agent/tasks/{task_id}/validate",
        json={"command": "python -m pytest", "approved": True},
    ).json()

    assert validated["status"] == "completed"
    assert validated["verification_status"] == "verified"
    assert validated["validation_result"]["success"] is True
    assert [item["status"] for item in validated["plan_details"]["tasks"]] == [
        "completed",
        "completed",
    ]
    assert all(
        item["verification_status"] == "verified"
        for item in validated["plan_details"]["tasks"]
    )


def test_passing_command_does_not_claim_implementation_criteria_met(
    agent_db,
    monkeypatch,
):
    payload = json.loads(make_structured_plan_response("python -m pytest"))
    payload["plan"]["tasks"] = payload["plan"]["tasks"][:1]
    monkeypatch.setattr(
        agent_router,
        "ai_service",
        FakeAIService(json.dumps(payload)),
    )
    monkeypatch.setattr(
        agent_router,
        "_run_project_validation_command",
        lambda **kwargs: (
            agent_router.TerminalContext(
                command=kwargs["command"],
                exit_code=0,
                stdout="1 passed",
                stderr="",
                success=True,
                timeout=False,
            ),
            [],
        ),
    )
    client = make_agent_client(agent_db, monkeypatch)
    task = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate and run tests"},
    ).json()
    task_id = task["id"]
    client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"accept_all": True},
    )

    validated = client.post(
        f"/ai/agent/tasks/{task_id}/validate",
        json={"command": "python -m pytest", "approved": True},
    ).json()

    assert validated["validation_result"]["success"] is True
    assert validated["status"] == "awaiting_validation"
    assert validated["verification_status"] == "not_verified"
    plan_task = validated["plan_details"]["tasks"][0]
    assert plan_task["status"] == "applied"
    assert plan_task["verification_status"] == "not_verified"
    assert plan_task["acceptance_results"][0]["status"] == "not_verified"

    completed = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-1",
            "verification_notes": "The acceptance criterion was checked separately.",
            "acceptance_results": acceptance_results_for(
                validated,
                "step-1",
                "Invoked calculate and confirmed the expected return value.",
            ),
        },
    ).json()
    assert completed["status"] == "completed"
    assert completed["verification_status"] == "verified"


def test_unmet_acceptance_criterion_fails_task_with_evidence(agent_db, monkeypatch):
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(
        agent_router,
        "ai_service",
        FakeAIService(make_structured_plan_response()),
    )
    task = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate"},
    ).json()
    task_id = task["id"]
    applied = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"accept_all": True},
    ).json()
    criterion = applied["plan_details"]["tasks"][0]["acceptance_criteria"][0]

    mismatched = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-1",
            "verification_notes": "The check failed.",
            "acceptance_results": [{
                "criterion": "An unrelated criterion",
                "status": "met",
                "evidence": "No evidence.",
            }],
        },
    )
    assert mismatched.status_code == 422

    result = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-1",
            "verification_notes": "The requested behavior is not present.",
            "acceptance_results": [{
                "criterion": criterion,
                "status": "not_met",
                "evidence": "Calling calculate still returns the old value.",
            }],
        },
    ).json()

    assert result["status"] == "failed"
    assert result["verification_status"] == "failed"
    assert result["plan_details"]["tasks"][0]["status"] == "blocked"
    assert result["plan_details"]["tasks"][0]["acceptance_results"][0]["status"] == "not_met"
    assert result["validation_result"]["kind"] == "acceptance_criteria"


def test_unavailable_docker_reports_verification_as_not_verified(
    agent_db,
    monkeypatch,
):
    monkeypatch.setenv("ENABLE_SANDBOX", "false")
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(
        agent_router,
        "ai_service",
        FakeAIService(make_structured_plan_response("python -m pytest")),
    )
    task = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate and test it"},
    ).json()
    task_id = task["id"]
    applied = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"accept_all": True},
    ).json()
    ready = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-1",
            "verification_notes": "The requested behavior was checked manually.",
            "acceptance_results": acceptance_results_for(
                applied,
                "step-1",
                "Confirmed calculate returns the requested result.",
            ),
        },
    ).json()
    assert ready["status"] == "awaiting_approval"

    unavailable = client.post(
        f"/ai/agent/tasks/{task_id}/validate",
        json={"command": "python -m pytest", "approved": True},
    ).json()

    assert unavailable["status"] == "awaiting_validation"
    assert unavailable["verification_status"] == "not_verified"
    assert unavailable["validation_result"]["success"] is False
    assert unavailable["validation_result"]["executed"] is False
    assert unavailable["plan_details"]["tasks"][1]["verification_status"] == "pending"

    manually_verified = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-2",
            "verification_notes": "The expected result was manually verified.",
            "acceptance_results": acceptance_results_for(
                unavailable,
                "step-2",
                "Executed the application and observed the expected result.",
            ),
        },
    ).json()
    assert manually_verified["status"] == "completed"
    assert manually_verified["verification_status"] == "verified"


def test_persistent_agent_store_recovers_active_work_as_paused(agent_db):
    persistent_sessions = sessionmaker(bind=agent_db.get_bind())
    first_store = AgentTaskStore(persistent_sessions)
    task = first_store.create(
        project_id=1,
        user_id=1,
        task="Update a validated function",
    )
    task.status = "executing"
    task.verification_status = "not_verified"
    task.plan_details = json.loads(make_structured_plan_response())["plan"]
    task.clarification_history = [{
        "questions": "Which application type?",
        "answer": "Command-line Python application",
    }]
    task.actions = [{"status": "applied", "action": action_payload()}]
    task.plan_details["tasks"][0]["status"] = "applied"
    task.plan_details["tasks"][0]["verification_status"] = "not_verified"
    task.plan_details["tasks"][0]["acceptance_results"] = [{
        "criterion": task.plan_details["tasks"][0]["acceptance_criteria"][0],
        "status": "not_verified",
        "evidence": "Validation output did not assess the criterion.",
    }]
    task.task_history = [{
        "plan_id": "test-plan",
        "iteration": 1,
        "plan_task_id": "step-1",
        "status": "applied",
        "description": "Approved changes were applied.",
        "created_at": datetime.now().isoformat(),
    }]
    task.changes = [{"path": "main.py", "content": "updated", "file_id": 11}]
    task.add_step("code_action", "completed", "Applied approved file change")

    recovered = AgentTaskStore(persistent_sessions).get(task.id, 1)

    assert recovered is not None
    assert recovered.status == "paused"
    assert recovered.verification_status == "not_verified"
    assert recovered.plan_details["plan_id"] == "test-plan"
    assert recovered.plan_details["tasks"][0]["status"] == "applied"
    assert recovered.plan_details["tasks"][0]["acceptance_results"][0]["status"] == "not_verified"
    assert recovered.task_history[0]["status"] == "applied"
    assert recovered.clarification_history[0]["answer"] == "Command-line Python application"
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
    ["rm -rf /", "python -m pytest ../../secrets", "pytest /etc/passwd", "npm install pkg", "git reset --hard", "pytest; whoami", "pytest -p malicious_plugin", "python -c print(1)", "python app.py --unsafe"],
)
def test_agent_command_validator_rejects_unsafe_commands(command):
    with pytest.raises(ValueError):
        validate_agent_command(command)


def test_agent_command_validator_allows_approved_project_checks():
    assert validate_agent_command("python -m pytest tests/test_auth.py") == "python -m pytest tests/test_auth.py"
    assert validate_agent_command("python hello.py") == "python hello.py"
    assert validate_agent_command("git diff") == "git diff"


def test_task_store_enforces_owner_and_cancellation():
    store = AgentTaskStore()
    task = store.create(1, 7, "Update validation")

    assert store.get(task.id, 8) is None
    assert store.claim_worker(task.id, 7) is task
    assert store.claim_worker(task.id, 7) is None
    store.release_worker(task.id)
    assert store.claim_worker(task.id, 7) is task
    store.release_worker(task.id)
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


def test_agent_project_intelligence_uses_manifests_tests_and_repository_conventions(
    agent_db,
):
    tests = ProjectFile(
        id=60,
        project_id=1,
        name="tests",
        type="folder",
        content=None,
        language=None,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    test_file = ProjectFile(
        id=61,
        project_id=1,
        parent_id=60,
        name="test_calculate.py",
        type="file",
        content="def test_calculate():\n    assert True\n",
        language="python",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    requirements = ProjectFile(
        id=62,
        project_id=1,
        name="requirements.txt",
        type="file",
        content="fastapi>=0.100\npytest==8.0\n",
        language="text",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    pytest_config = ProjectFile(
        id=63,
        project_id=1,
        name="pytest.ini",
        type="file",
        content="[pytest]\n",
        language="ini",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    readme = ProjectFile(
        id=64,
        project_id=1,
        name="README.md",
        type="file",
        content="# Owned\n\npython main.py\n",
        language="markdown",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    agent_db.add_all([tests, test_file, requirements, pytest_config, readme])
    agent_db.commit()

    profile = json.loads(AgentTools(agent_db, 1, 1).project_intelligence())

    assert profile["project"]["name"] == "Owned"
    assert len(json.dumps(profile)) <= AgentTools.MAX_PROJECT_PROFILE_CHARS
    assert "main.py" in profile["entry_point_candidates"]
    assert "tests/test_calculate.py" in profile["test_file_candidates"]
    assert "pytest.ini" in profile["convention_files"]
    assert profile["detected_frameworks"] == ["FastAPI"]
    assert profile["readme_commands"] == [{
        "path": "README.md",
        "commands": "python main.py",
    }]
    assert {
        dependency
        for manifest in profile["manifests"]
        for dependency in manifest.get("dependencies", [])
    } >= {"fastapi", "pytest"}


def test_agent_retrieval_combines_vector_and_lexical_context(agent_db, monkeypatch):
    class FakeRelevance:
        def __init__(self, **kwargs):
            pass

        def build_context(self, query):
            return SimpleNamespace(search_results=[
                SimpleNamespace(path="lexical.py", content="lexical evidence"),
                SimpleNamespace(path="shared.py", content="duplicate lexical evidence"),
            ])

    class FakeVector:
        def __init__(self, **kwargs):
            pass

        def search_project_context(self, project_id, query, top_k):
            return SimpleNamespace(chunks=[
                SimpleNamespace(
                    file_path="vector.py",
                    start_line=1,
                    end_line=2,
                    language="python",
                    content="vector evidence",
                ),
                SimpleNamespace(
                    file_path="shared.py",
                    start_line=1,
                    end_line=2,
                    language="python",
                    content="shared vector evidence",
                ),
            ])

    monkeypatch.setattr(
        agent_tools_module,
        "RepositoryRelevanceService",
        FakeRelevance,
    )
    monkeypatch.setattr(agent_tools_module, "VectorRAGService", FakeVector)

    context = AgentTools(agent_db, 1, 1).retrieve_context("find evidence")

    assert "vector evidence" in context
    assert "lexical evidence" in context
    assert "shared vector evidence" in context
    assert "duplicate lexical evidence" not in context


def test_agent_tool_protocol_reads_files_and_finishes_read_only_request(
    agent_db,
    monkeypatch,
):
    responses = [
        json.dumps({
            "type": "tool_call",
            "name": "list_files",
            "arguments": {"path": ""},
        }),
        json.dumps({
            "type": "tool_call",
            "name": "read_file",
            "arguments": {"path": "main.py"},
        }),
        json.dumps({
            "plan": ["main.py defines calculate and currently returns 1."],
            "actions": [],
            "validation_command": None,
        }),
    ]
    client = make_agent_client(agent_db, monkeypatch)
    fake_ai = FakeAIService(responses)
    monkeypatch.setattr(agent_router, "ai_service", fake_ai)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Read main.py and explain it"},
    )
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()

    assert task["status"] == "completed"
    assert task["tool_calls_used"] == 2
    assert task["plan"] == ["main.py defines calculate and currently returns 1."]
    assert [step["description"] for step in task["steps"] if step["type"] == "tool"] == [
        "list_files completed",
        "read_file completed",
    ]
    assert len(fake_ai.messages) == 3


def test_agent_requests_material_clarification_before_proposing_changes(
    agent_db,
    monkeypatch,
):
    question = "Should this be a command-line app or a web application?"
    responses = [
        json.dumps({
            "status": "clarification_required",
            "plan": None,
            "clarification_questions": [question],
            "actions": [],
            "validation_command": None,
        }),
        make_plan_response(),
    ]
    fake_ai = FakeAIService(responses)
    monkeypatch.setattr(agent_router, "ai_service", fake_ai)
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Create an employee management system"},
    )
    task_id = created.json()["id"]
    waiting = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert waiting["status"] == "awaiting_clarification"
    assert waiting["clarification_questions"] == [question]
    assert waiting["actions"] == []

    resumed = client.post(
        f"/ai/agent/tasks/{task_id}/clarify",
        json={"answer": "Make it a command-line Python application."},
    )
    assert resumed.status_code == 200
    planned = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert planned["status"] == "awaiting_approval"
    assert planned["plan_details"]["requirements"]
    assert "command-line Python application" in fake_ai.messages[-1]
    assert "Name: Owned" in fake_ai.contexts[0]
    assert "REPOSITORY INTELLIGENCE" in fake_ai.contexts[0]
    assert '"entry_point_candidates": ["main.py"]' in fake_ai.contexts[0]


def test_agent_create_file_tool_requires_approval_and_persists_afterward(
    agent_db,
    monkeypatch,
):
    responses = [
        json.dumps({
            "type": "tool_call",
            "name": "create_file",
            "arguments": {
                "path": "hello.py",
                "content": "print('Hello World')\n",
            },
        }),
        json.dumps({
            "plan": ["Created hello.py as requested."],
            "actions": [],
            "validation_command": None,
        }),
    ]
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(responses))

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Create hello.py"},
    )
    task_id = created.json()["id"]
    waiting = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert waiting["status"] == "awaiting_approval"
    assert waiting["actions"][0]["action"]["operation"] == "create_file"
    assert waiting["actions"][0]["status"] == "awaiting_approval"
    assert (
        agent_db.query(ProjectFile)
        .filter(ProjectFile.project_id == 1, ProjectFile.name == "hello.py")
        .first()
        is None
    )

    approved = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"action_indexes": [0]},
    ).json()
    assert approved["actions"][0]["status"] == "applied"
    completed = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert completed["status"] == "completed"
    created_file = (
        agent_db.query(ProjectFile)
        .filter(ProjectFile.project_id == 1, ProjectFile.name == "hello.py")
        .one()
    )
    assert created_file.content == "print('Hello World')\n"


def test_agent_update_file_tool_requires_approval(agent_db, monkeypatch):
    updated_content = "def calculate():\n    return 42\n"
    responses = [
        json.dumps({
            "type": "tool_call",
            "name": "update_file",
            "arguments": {"path": "main.py", "content": updated_content},
        }),
        json.dumps({
            "plan": ["Updated main.py."],
            "actions": [],
            "validation_command": None,
        }),
    ]
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(responses))

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update main.py"},
    )
    task_id = created.json()["id"]
    waiting = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert waiting["status"] == "awaiting_approval"
    assert waiting["actions"][0]["action"]["operation"] == "replace"
    assert agent_db.get(ProjectFile, 11).content.endswith("return 1\n")

    approved = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"action_indexes": [0]},
    ).json()

    assert approved["actions"][0]["status"] == "applied"
    assert approved["changes"][0]["content"] == updated_content
    assert agent_db.get(ProjectFile, 11).content == updated_content


def test_agent_delete_file_tool_requires_explicit_approval(agent_db, monkeypatch):
    responses = [
        json.dumps({
            "type": "tool_call",
            "name": "delete_file",
            "arguments": {"path": "main.py"},
        }),
        json.dumps({
            "plan": ["Deleted main.py after approval."],
            "actions": [],
            "validation_command": None,
        }),
    ]
    client = make_agent_client(agent_db, monkeypatch)
    monkeypatch.setattr(agent_router, "ai_service", FakeAIService(responses))

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Delete main.py"},
    )
    task_id = created.json()["id"]
    waiting = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert waiting["status"] == "awaiting_approval"
    assert waiting["actions"][0]["action"]["operation"] == "delete"
    assert (
        agent_db.query(ProjectFile)
        .filter(ProjectFile.project_id == 1, ProjectFile.name == "main.py")
        .one_or_none()
        is not None
    )

    approved = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"action_indexes": [0]},
    ).json()

    assert approved["actions"][0]["status"] == "applied"
    assert approved["changes"][0]["deleted"] is True
    assert (
        agent_db.query(ProjectFile)
        .filter(ProjectFile.project_id == 1, ProjectFile.name == "main.py")
        .one_or_none()
        is None
    )


def test_agent_tool_argument_errors_are_returned_to_model(agent_db, monkeypatch):
    responses = [
        json.dumps({
            "type": "tool_call",
            "name": "read_file",
            "arguments": {},
        }),
        json.dumps({
            "plan": ["The requested file could not be read."],
            "actions": [],
            "validation_command": None,
        }),
    ]
    client = make_agent_client(agent_db, monkeypatch)
    fake_ai = FakeAIService(responses)
    monkeypatch.setattr(agent_router, "ai_service", fake_ai)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Read main.py"},
    )
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()

    assert task["status"] == "completed"
    failed_tool = next(step for step in task["steps"] if step["type"] == "tool")
    assert failed_tool["status"] == "failed"
    assert "Invalid arguments for read_file" in failed_tool["error"]
    assert "Invalid arguments for read_file" in fake_ai.messages[1]


def test_agent_tool_loop_stops_at_configured_call_limit(agent_db, monkeypatch):
    tool_call = json.dumps({
        "type": "tool_call",
        "name": "list_files",
        "arguments": {"path": ""},
    })
    monkeypatch.setattr(agent_router, "AI_AGENT_MAX_TOOL_CALLS", 2)
    monkeypatch.setattr(
        agent_router,
        "ai_service",
        FakeAIService([tool_call, tool_call, tool_call]),
    )
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "List project files"},
    )
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()

    assert task["status"] == "failed"
    assert task["tool_calls_used"] == 2
    assert "configured limit of 2 tool calls" in task["stop_reason"]


def test_agent_task_denies_projects_owned_by_another_user(agent_db, monkeypatch):
    client = make_agent_client(agent_db, monkeypatch, owner_id=1)

    response = client.post(
        "/ai/agent/tasks",
        json={"project_id": 2, "task": "Read private.py"},
    )

    assert response.status_code == 404


def test_agent_provider_failure_is_persisted_as_failed_task(agent_db, monkeypatch):
    class FailingAIService:
        async def chat_json(self, *_args, **_kwargs):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(agent_router, "ai_service", FailingAIService())
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Inspect the project"},
    )
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()

    assert task["status"] == "failed"
    assert task["stop_reason"] == "provider unavailable"
    assert any(
        step["status"] == "failed" and "provider unavailable" in (step["error"] or "")
        for step in task["steps"]
    )


def test_unexpected_worker_exit_cannot_leave_task_planning(agent_db, monkeypatch):
    monkeypatch.setattr(
        agent_router,
        "_run_planning_task",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("worker crashed")),
    )
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Inspect the project"},
    )
    task = client.get(f"/ai/agent/tasks/{created.json()['id']}").json()

    assert task["status"] == "failed"
    assert task["stop_reason"] == "worker crashed"
    assert task["steps"][-1]["description"] == "Agent planning worker failed"


def test_agent_tools_allow_auth_code_that_generates_tokens(agent_db):
    auth_file = ProjectFile(
        id=13,
        project_id=1,
        name="auth.py",
        type="file",
        content=(
            "def create_token(user):\n"
            "    access_token = jwt.encode({'sub': user.username}, SECRET_KEY)\n"
            "    return access_token\n"
        ),
        language="python",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    agent_db.add(auth_file)
    agent_db.commit()
    tools = AgentTools(agent_db, 1, 1)

    inspected = tools.inspect_file("auth.py")
    results = tools.search_project("access token")

    assert inspected["content"] == auth_file.content
    assert any(result["path"] == "auth.py" for result in results)


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


def test_agent_approval_preserves_file_if_repository_reindexing_fails(
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
    assert applied.json()["status"] == "awaiting_validation"
    assert applied.json()["plan_details"]["tasks"][0]["status"] == "applied"
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
    plan_outputs = [
        step["output"]
        for step in task["steps"]
        if step["type"] == "plan" and step["output"]
    ]
    assert len(plan_outputs) == 2
    assert json.loads(plan_outputs[0])["actions"][0].get("old_code") is None
    assert json.loads(plan_outputs[1])["actions"][0]["old_code"] == "    return 1\n"
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
    assert "Use operation=create_file with new_code" in service.messages[1]
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


def test_agent_retry_preserves_prior_applied_file_changes(agent_db, monkeypatch):
    helper_file = ProjectFile(
        id=14,
        project_id=1,
        name="helpers.py",
        type="file",
        content="def helper():\n    return 1\n",
        language="python",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    agent_db.add(helper_file)
    agent_db.commit()
    initial_response = json.dumps({
        "plan": ["Update the entry point", "Update the helper"],
        "actions": [
            action_payload(),
            {
                **action_payload(),
                "file_path": "helpers.py",
                "old_code": "    return 1\n",
                "new_code": "    return 2\n",
            },
        ],
        "validation_command": None,
    })
    corrected_helper = {
        **action_payload(),
        "file_path": "helpers.py",
        "old_code": "    return 1\n",
        "new_code": "    return 10\n",
        "description": "Reapply the helper change against its current contents",
    }
    service = FakeAIService([
        initial_response,
        json.dumps({
            "plan": ["Update the helper"],
            "actions": [corrected_helper],
            "validation_command": None,
        }),
    ])
    monkeypatch.setattr(agent_router, "ai_service", service)
    client = make_agent_client(agent_db, monkeypatch)

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update the entry point and helper"},
    ).json()
    task_id = created["id"]

    class FailingHelperActionService:
        def __init__(self, db):
            self.service = CodeActionService(db)

        def apply_action(self, project_id, file_id, user_id, action):
            if action.file_path == "helpers.py":
                raise PatchNotFoundError("Simulated helper patch failure.")
            return self.service.apply_action(
                project_id=project_id,
                file_id=file_id,
                user_id=user_id,
                action=action,
            )

    monkeypatch.setattr(
        agent_router,
        "CodeActionService",
        FailingHelperActionService,
    )
    partial = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"accept_all": True},
    ).json()

    assert partial["status"] == "failed"
    assert [item["status"] for item in partial["actions"]] == ["applied", "failed"]
    assert agent_db.get(ProjectFile, 11).content.endswith("return 2\n")
    monkeypatch.setattr(
        agent_router,
        "CodeActionService",
        lambda db: CodeActionService(db),
    )

    retried = client.post(f"/ai/agent/tasks/{task_id}/continue")
    recovered = client.get(f"/ai/agent/tasks/{task_id}").json()

    assert retried.status_code == 202
    assert recovered["status"] == "awaiting_approval"
    assert [item["status"] for item in recovered["actions"]] == [
        "applied",
        "awaiting_approval",
    ]
    assert recovered["actions"][0]["action"]["file_path"] == "main.py"
    assert recovered["actions"][1]["action"]["file_path"] == "helpers.py"
    assert any(entry["iteration"] == 0 for entry in recovered["task_history"])
    assert any(entry["iteration"] == 1 for entry in recovered["task_history"])

    correction = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"action_indexes": [1]},
    ).json()
    assert correction["actions"][0]["status"] == "applied"
    assert correction["actions"][1]["status"] == "applied"
    assert agent_db.get(ProjectFile, 11).content.endswith("return 2\n")
    assert agent_db.get(ProjectFile, 14).content.endswith("return 10\n")


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
    assert response.json()["status"] == "awaiting_validation"
    assert response.json()["plan_details"]["tasks"][0]["status"] == "applied"
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
    resume = other_client.post(f"/ai/agent/tasks/{task['id']}/continue")

    assert response.status_code == 404
    assert resume.status_code == 404


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


def test_agent_validation_does_not_fall_back_to_host_without_docker(monkeypatch):
    monkeypatch.setenv("ENABLE_SANDBOX", "true")
    monkeypatch.setattr(agent_router.sandbox_service, "is_available", lambda: False)

    result = agent_router._run_validation_command(1, "python -m pytest")

    assert result.success is False
    assert result.executed is False
    assert result.exit_code == 127
    assert "not executed on the host" in result.stderr


def test_agent_command_tool_requires_approval_and_returns_sandbox_output(
    agent_db,
    monkeypatch,
):
    responses = [
        json.dumps({
            "type": "tool_call",
            "name": "execute_command",
            "arguments": {"command": "python hello.py"},
        }),
        json.dumps({
            "plan": ["Ran hello.py and received Hello World."],
            "actions": [],
            "validation_command": None,
        }),
    ]
    client = make_agent_client(agent_db, monkeypatch)
    fake_ai = FakeAIService(responses)
    monkeypatch.setattr(agent_router, "ai_service", fake_ai)
    monkeypatch.setattr(
        agent_router,
        "_run_project_validation_command",
        lambda **kwargs: (
            agent_router.TerminalContext(
                command=kwargs["command"],
                exit_code=0,
                stdout="Hello World\n",
                stderr="",
                success=True,
                timeout=False,
            ),
            [],
        ),
    )

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Run hello.py"},
    )
    task_id = created.json()["id"]
    waiting = client.get(f"/ai/agent/tasks/{task_id}").json()

    assert waiting["status"] == "awaiting_approval"
    assert waiting["validation_command"] == "python hello.py"
    assert waiting["tool_calls_used"] == 1

    approved = client.post(
        f"/ai/agent/tasks/{task_id}/validate",
        json={"command": "python hello.py", "approved": True},
    )
    assert approved.status_code == 200
    completed = client.get(f"/ai/agent/tasks/{task_id}").json()

    assert completed["status"] == "completed"
    assert completed["validation_result"]["stdout"] == "Hello World\n"
    assert "Hello World" in fake_ai.messages[1]


def test_agent_sandbox_failure_result_is_returned_to_model(agent_db, monkeypatch):
    responses = [
        json.dumps({
            "type": "tool_call",
            "name": "execute_command",
            "arguments": {"command": "python hello.py"},
        }),
        json.dumps({
            "plan": ["The script failed with a syntax error."],
            "actions": [],
            "validation_command": None,
        }),
    ]
    client = make_agent_client(agent_db, monkeypatch)
    fake_ai = FakeAIService(responses)
    monkeypatch.setattr(agent_router, "ai_service", fake_ai)
    monkeypatch.setattr(
        agent_router,
        "_run_project_validation_command",
        lambda **kwargs: (
            agent_router.TerminalContext(
                command=kwargs["command"],
                exit_code=1,
                stdout="",
                stderr="SyntaxError: invalid syntax",
                success=False,
                timeout=False,
            ),
            [],
        ),
    )

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Run hello.py"},
    )
    task_id = created.json()["id"]
    approved = client.post(
        f"/ai/agent/tasks/{task_id}/validate",
        json={"command": "python hello.py", "approved": True},
    )

    assert approved.status_code == 200
    completed = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert completed["status"] == "completed"
    assert completed["validation_result"]["success"] is False
    assert "SyntaxError: invalid syntax" in fake_ai.messages[1]
    assert any(
        step["status"] == "failed" and "SyntaxError" in (step["output"] or "")
        for step in completed["steps"]
        if step["type"] == "tool"
    )


def test_agent_create_approve_and_run_completes_through_real_docker(
    agent_db,
    monkeypatch,
    tmp_path,
):
    from App.service import project_workspace_service
    from App.service.sandbox_service import SandboxService

    sandbox = SandboxService(
        workspace_root=str(tmp_path / "workspaces"),
        sandbox_root=str(tmp_path / "temporary" / "sandbox"),
    )
    if not sandbox.is_available():
        pytest.skip("Docker sandbox image is unavailable.")

    class NoopRepositoryIndexer:
        def __init__(self, *_args, **_kwargs):
            pass

        def index_file_after_save(self, _file):
            pass

    class NoopVectorService:
        def __init__(self, *_args, **_kwargs):
            pass

        def delete_files(self, _file_ids):
            pass

    monkeypatch.setattr(agent_router, "sandbox_service", sandbox)
    monkeypatch.setattr(
        project_workspace_service,
        "RepositoryIndexService",
        NoopRepositoryIndexer,
    )
    monkeypatch.setattr(
        project_workspace_service,
        "VectorRAGService",
        NoopVectorService,
    )
    delete_target = ProjectFile(
        id=13,
        project_id=1,
        name="delete-me.py",
        type="file",
        content="print('temporary')\n",
        language="python",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    agent_db.add(delete_target)
    agent_db.commit()
    fake_ai = FakeAIService([
        json.dumps({
            "type": "tool_call",
            "name": "create_file",
            "arguments": {
                "path": "hello.py",
                "content": "print('Hello World')\n",
            },
        }),
        json.dumps({
            "type": "tool_call",
            "name": "execute_command",
            "arguments": {"command": "python hello.py"},
        }),
        json.dumps({
            "plan": ["Created hello.py and ran it successfully."],
            "actions": [],
            "validation_command": None,
        }),
        json.dumps({
            "type": "tool_call",
            "name": "update_file",
            "arguments": {
                "path": "hello.py",
                "content": "print('Hello AI')\n",
            },
        }),
        json.dumps({
            "type": "tool_call",
            "name": "execute_command",
            "arguments": {"command": "python hello.py"},
        }),
        json.dumps({
            "plan": ["Updated hello.py and confirmed Hello AI output."],
            "actions": [],
            "validation_command": None,
        }),
        json.dumps({
            "type": "tool_call",
            "name": "update_file",
            "arguments": {
                "path": "hello.py",
                "content": "print('broken'\n",
            },
        }),
        json.dumps({
            "type": "tool_call",
            "name": "execute_command",
            "arguments": {"command": "python hello.py"},
        }),
        json.dumps({
            "type": "tool_call",
            "name": "update_file",
            "arguments": {
                "path": "hello.py",
                "content": "print('fixed')\n",
            },
        }),
        json.dumps({
            "type": "tool_call",
            "name": "execute_command",
            "arguments": {"command": "python hello.py"},
        }),
        json.dumps({
            "plan": ["Corrected the syntax error and confirmed the script runs."],
            "actions": [],
            "validation_command": None,
        }),
        json.dumps({
            "type": "tool_call",
            "name": "delete_file",
            "arguments": {"path": "delete-me.py"},
        }),
        json.dumps({
            "plan": ["Deleted delete-me.py after user approval."],
            "actions": [],
            "validation_command": None,
        }),
    ])
    monkeypatch.setattr(agent_router, "ai_service", fake_ai)
    client = make_agent_client(
        agent_db,
        monkeypatch,
        sync_workspace=True,
    )

    created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Create hello.py and run it"},
    )
    task_id = created.json()["id"]
    create_pending = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert create_pending["status"] == "awaiting_approval"

    file_approved = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"action_indexes": [0]},
    )
    assert file_approved.status_code == 200
    command_pending = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert command_pending["status"] == "awaiting_approval"
    assert command_pending["validation_command"] == "python hello.py"

    command_approved = client.post(
        f"/ai/agent/tasks/{task_id}/validate",
        json={"command": "python hello.py", "approved": True},
    )
    assert command_approved.status_code == 200
    completed = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert completed["status"] == "completed"
    assert completed["validation_result"]["stdout"].strip() == "Hello World"
    assert any(
        "Hello World" in step["output"]
        for step in completed["steps"]
        if step["type"] == "tool"
    )
    assert len(fake_ai.messages) == 3
    project_file = (
        agent_db.query(ProjectFile)
        .filter(
            ProjectFile.project_id == 1,
            ProjectFile.name == "hello.py",
        )
        .one()
    )
    assert project_file.content == "print('Hello World')\n"
    assert (
        sandbox.get_project_directory(1) / "hello.py"
    ).read_text(encoding="utf-8") == project_file.content

    update_created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update hello.py and run it again"},
    )
    update_id = update_created.json()["id"]
    update_pending = client.get(f"/ai/agent/tasks/{update_id}").json()
    assert update_pending["status"] == "awaiting_approval"
    assert client.post(
        f"/ai/agent/tasks/{update_id}/approve",
        json={"action_indexes": [0]},
    ).status_code == 200
    update_command = client.get(f"/ai/agent/tasks/{update_id}").json()
    assert update_command["status"] == "awaiting_approval"
    update_result = client.post(
        f"/ai/agent/tasks/{update_id}/validate",
        json={"command": "python hello.py", "approved": True},
    )
    assert update_result.status_code == 200
    update_completed = client.get(f"/ai/agent/tasks/{update_id}").json()
    assert update_completed["status"] == "completed"
    assert update_completed["validation_result"]["stdout"].strip() == "Hello AI"
    assert agent_db.get(ProjectFile, project_file.id).content == "print('Hello AI')\n"
    assert (
        sandbox.get_project_directory(1) / "hello.py"
    ).read_text(encoding="utf-8") == "print('Hello AI')\n"

    correction_created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Break hello.py, test it, fix the syntax error, and rerun"},
    )
    correction_id = correction_created.json()["id"]
    broken_change = client.get(f"/ai/agent/tasks/{correction_id}").json()
    assert broken_change["status"] == "awaiting_approval"
    client.post(
        f"/ai/agent/tasks/{correction_id}/approve",
        json={"action_indexes": [0]},
    )
    failed_command = client.get(f"/ai/agent/tasks/{correction_id}").json()
    assert failed_command["status"] == "awaiting_approval"
    failed_result = client.post(
        f"/ai/agent/tasks/{correction_id}/validate",
        json={"command": "python hello.py", "approved": True},
    )
    assert failed_result.status_code == 200
    corrected_change = client.get(f"/ai/agent/tasks/{correction_id}").json()
    assert corrected_change["status"] == "awaiting_approval"
    assert any(
        step["status"] == "failed"
        and "SyntaxError" in (step["output"] or "")
        for step in corrected_change["steps"]
        if step["type"] == "tool"
    )
    correction_approved = client.post(
        f"/ai/agent/tasks/{correction_id}/approve",
        json={"action_indexes": [1]},
    )
    assert correction_approved.status_code == 200
    corrected_command = client.get(f"/ai/agent/tasks/{correction_id}").json()
    assert corrected_command["status"] == "awaiting_approval"
    assert corrected_command["validation_command"] == "python hello.py"
    rerun = client.post(
        f"/ai/agent/tasks/{correction_id}/validate",
        json={"command": "python hello.py", "approved": True},
    )
    assert rerun.status_code == 200
    correction_completed = client.get(f"/ai/agent/tasks/{correction_id}").json()
    assert correction_completed["status"] == "completed"
    assert correction_completed["validation_result"]["stdout"].strip() == "fixed"
    assert agent_db.get(ProjectFile, project_file.id).content == "print('fixed')\n"
    assert (
        sandbox.get_project_directory(1) / "hello.py"
    ).read_text(encoding="utf-8") == "print('fixed')\n"

    delete_created = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Delete delete-me.py"},
    )
    delete_id = delete_created.json()["id"]
    delete_pending = client.get(f"/ai/agent/tasks/{delete_id}").json()
    assert delete_pending["status"] == "awaiting_approval"
    assert agent_db.get(ProjectFile, 13) is not None
    assert (
        sandbox.get_project_directory(1) / "delete-me.py"
    ).is_file()
    deleted = client.post(
        f"/ai/agent/tasks/{delete_id}/approve",
        json={"action_indexes": [0]},
    )
    assert deleted.status_code == 200
    delete_completed = client.get(f"/ai/agent/tasks/{delete_id}").json()
    assert delete_completed["status"] == "completed"
    assert agent_db.get(ProjectFile, 13) is None
    assert not (
        sandbox.get_project_directory(1) / "delete-me.py"
    ).exists()
    repeated_delete_approval = client.post(
        f"/ai/agent/tasks/{delete_id}/approve",
        json={"action_indexes": [0]},
    )
    assert repeated_delete_approval.status_code == 409
    repeated_command_approval = client.post(
        f"/ai/agent/tasks/{task_id}/validate",
        json={"command": "python hello.py", "approved": True},
    )
    assert repeated_command_approval.status_code == 409
    assert len(fake_ai.messages) == 13


def test_agent_failure_recovery_requires_approval_and_completes(agent_db, monkeypatch):
    correction = {
        **action_payload(),
        "old_code": "    return 2\n",
        "new_code": "    return 3\n",
        "description": "Correct the return value",
    }
    invalid_correction = json.dumps({
        "plan": ["Correct the function after failed validation"],
        "actions": [{
            **correction,
            "old_code": "",
        }],
        "validation_command": "python -m pytest",
    })
    responses = [
        make_plan_response("python -m pytest"),
        invalid_correction,
        invalid_correction,
        json.dumps({
            "plan": ["Correct the function after failed validation"],
            "actions": [correction],
            "validation_command": "python -m pytest",
        }),
    ]
    client = make_agent_client(agent_db, monkeypatch)
    service = FakeAIService(responses)
    monkeypatch.setattr(agent_router, "ai_service", service)
    monkeypatch.setattr(agent_router, "AI_AGENT_MAX_CORRECTION_RETRIES", 1)
    monkeypatch.setattr(agent_router.sandbox_service, "is_available", lambda: True)
    terminal_results = iter([
        SimpleNamespace(
            exit_code=1,
            stdout="",
            stderr="assertion failed",
            success=False,
            timeout=False,
            created_files=[],
            modified_files=[],
            deleted_files=[],
        ),
        SimpleNamespace(
            exit_code=0,
            stdout="2 passed",
            stderr="",
            success=True,
            timeout=False,
            created_files=[],
            modified_files=[],
            deleted_files=[],
        ),
    ])
    def fake_project_validation(db, project_id, user_id, command, timeout_seconds=120):
        result = next(terminal_results)
        return (
            agent_router.TerminalContext(
                command=command,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                success=result.success,
                timeout=result.timeout,
            ),
            [],
        )

    monkeypatch.setattr(
        agent_router,
        "_run_project_validation_command",
        fake_project_validation,
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
    assert applied["status"] == "awaiting_validation"
    applied = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-1",
            "verification_notes": "The requested change is ready for validation.",
            "acceptance_results": acceptance_results_for(
                applied,
                "step-1",
                "Confirmed the requested change is implemented.",
            ),
        },
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
    failed_correction = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert failed_correction["status"] == "failed"
    assert failed_correction["actions"][0]["status"] == "applied"
    assert sum(
        step["description"] == "Repairing invalid correction proposal"
        for step in failed_correction["steps"]
    ) == 1
    assert "COMPLETED WORK TO PRESERVE" in service.messages[1]
    assert "completed_plan_tasks" in service.messages[1]
    assert "empty old_code is only valid at line 1" in service.messages[2]
    assert "COMPLETED WORK TO PRESERVE" in service.messages[2]

    retried = client.post(f"/ai/agent/tasks/{task_id}/continue")
    assert retried.status_code == 202
    correction_task = client.get(f"/ai/agent/tasks/{task_id}").json()
    assert correction_task["status"] == "awaiting_approval"
    assert correction_task["iteration"] == 2
    assert any(
        entry["iteration"] == 0
        and entry["plan_task_id"] == "step-1"
        and entry["status"] == "completed"
        for entry in correction_task["task_history"]
    )
    assert any(
        entry["iteration"] == 2
        and entry["plan_task_id"] == "step-1"
        and entry["status"] == "in_progress"
        for entry in correction_task["task_history"]
    )

    correction_applied = client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"action_indexes": [1]},
    ).json()
    assert correction_applied["status"] == "awaiting_approval"
    assert [item["status"] for item in correction_applied["actions"]] == [
        "applied",
        "applied",
    ]
    assert any(
        entry["iteration"] == 2
        and entry["plan_task_id"] == "step-1"
        and entry["status"] == "applied"
        for entry in correction_applied["task_history"]
    )
    assert len(service.messages) == 4
    completed = client.post(
        f"/ai/agent/tasks/{task_id}/validate",
        json={"command": "python -m pytest", "approved": True},
    ).json()

    assert completed["status"] == "awaiting_validation"
    completed = client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-1",
            "verification_notes": "The corrected behavior satisfies the criterion.",
            "acceptance_results": acceptance_results_for(
                completed,
                "step-1",
                "Confirmed calculate returns the corrected value.",
            ),
        },
    ).json()
    assert completed["status"] == "completed"
    assert completed["validation_result"]["success"] is True
    assert agent_db.get(ProjectFile, 11).content.endswith("return 3\n")


def test_agent_correction_clarification_preserves_applied_work(agent_db, monkeypatch):
    correction = {
        **action_payload(),
        "old_code": "    return 2\n",
        "new_code": "    return 3\n",
        "description": "Correct the return value",
    }
    clarification = json.dumps({
        "status": "clarification_required",
        "plan": None,
        "clarification_questions": ["Should the correction preserve the existing return type?"],
        "actions": [],
        "validation_command": None,
    })
    service = FakeAIService([
        make_structured_plan_response("python -m pytest"),
        clarification,
        make_structured_plan_response("python -m pytest", [correction]),
    ])
    monkeypatch.setattr(agent_router, "ai_service", service)
    monkeypatch.setattr(
        agent_router,
        "_run_project_validation_command",
        lambda **kwargs: (
            agent_router.TerminalContext(
                command=kwargs["command"],
                exit_code=1,
                stdout="",
                stderr="assertion failed",
                success=False,
                timeout=False,
            ),
            [],
        ),
    )
    client = make_agent_client(agent_db, monkeypatch)
    task = client.post(
        "/ai/agent/tasks",
        json={"project_id": 1, "task": "Update calculate and verify it"},
    ).json()
    task_id = task["id"]
    client.post(
        f"/ai/agent/tasks/{task_id}/approve",
        json={"accept_all": True},
    )
    client.post(
        f"/ai/agent/tasks/{task_id}/plan-tasks/complete",
        json={
            "plan_task_id": "step-1",
            "verification_notes": "The original implementation task was applied.",
            "acceptance_results": acceptance_results_for(
                client.get(f"/ai/agent/tasks/{task_id}").json(),
                "step-1",
                "Confirmed the original implementation behavior.",
            ),
        },
    )
    client.post(
        f"/ai/agent/tasks/{task_id}/validate",
        json={"command": "python -m pytest", "approved": True},
    )

    client.post(f"/ai/agent/tasks/{task_id}/continue")
    awaiting_clarification = client.get(
        f"/ai/agent/tasks/{task_id}"
    ).json()
    assert awaiting_clarification["status"] == "awaiting_clarification"
    assert awaiting_clarification["actions"][0]["status"] == "applied"
    assert awaiting_clarification["plan_details"]["tasks"][0]["status"] == "completed"
    assert any(
        entry["status"] == "completed"
        for entry in awaiting_clarification["task_history"]
    )

    clarified = client.post(
        f"/ai/agent/tasks/{task_id}/clarify",
        json={"answer": "Keep the existing return type."},
    )
    recovered = client.get(f"/ai/agent/tasks/{task_id}").json()

    assert clarified.status_code == 200
    assert recovered["status"] == "awaiting_approval"
    assert [item["status"] for item in recovered["actions"]] == [
        "applied",
        "awaiting_approval",
    ]
    assert any(
        step["type"] == "clarification"
        and step["input"] == "Keep the existing return type."
        for step in recovered["steps"]
    )
    assert any(
        entry["iteration"] == 0
        and entry.get("verification_notes")
        == "The original implementation task was applied."
        for entry in recovered["task_history"]
    )