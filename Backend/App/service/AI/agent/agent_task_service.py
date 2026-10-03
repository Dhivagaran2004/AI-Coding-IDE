from typing import cast
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from App.schema.ai_schema import CodeAction


MAX_AGENT_ITERATIONS = 5
MAX_AGENT_PLAN_STEPS = 12
MAX_AGENT_ACTIONS = 10
MAX_AGENT_CONTEXT_CHARS = 30000
MAX_AGENT_OUTPUT_CHARS = 12000
MAX_STORED_AGENT_TASKS = 200


@dataclass
class AgentStep:
    sequence: int
    type: str
    status: str
    description: str
    input: str = ""
    output: str = ""
    error: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "type": self.type,
            "status": self.status,
            "created_at": self.created_at,
            "description": self.description,
            "input": self.input[:2000],
            "output": self.output[:MAX_AGENT_OUTPUT_CHARS],
            "error": self.error[:2000] if self.error else None,
            "created_at": self.created_at,
        }


@dataclass
class AgentTask:
    id: str
    project_id: int
    user_id: int
    task: str
    current_context: str = ""
    status: str = "pending"
    plan: list[str] = field(default_factory=lambda: list[str]())
    actions: list[dict[str, object]] = field(
        default_factory=lambda: list[dict[str, object]]()
    )
    validation_command: str | None = None
    validation_result: dict[str, object] | None = None
    change_history: list[dict[str, object]] = field(
        default_factory=lambda: list[dict[str, object]]()
    )
    steps: list[AgentStep] = field(default_factory=lambda: list[AgentStep]())
    stop_reason: str | None = None
    iteration: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def add_step(
        self,
        step_type: str,
        status: str,
        description: str,
        **values: str,
    ) -> None:
        self.steps.append(
            AgentStep(
                sequence=len(self.steps) + 1,
                type=step_type,
                status=status,
                description=description,
                **values,
            )
        )
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "task": self.task,
            "mode": "agent",
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "plan": self.plan[:MAX_AGENT_PLAN_STEPS],
            "actions": self.actions[:MAX_AGENT_ACTIONS],
            "validation_command": self.validation_command,
            "validation_result": self.validation_result,
            "change_history": self.change_history[-20:],
            "iteration": self.iteration,
            "steps": [step.as_dict() for step in self.steps[-50:]],
            "stop_reason": self.stop_reason,
        }


class AgentTaskStore:
    """Process-local task state for short-lived agent workflows."""

    def __init__(self):
        self._tasks: dict[str, AgentTask] = {}
        self._lock = RLock()

    def create(
        self,
        project_id: int,
        user_id: int,
        task: str,
        current_context: str = "",
    ) -> AgentTask:
        with self._lock:
            if len(self._tasks) >= MAX_STORED_AGENT_TASKS:
                finished = sorted(
                    (
                        task for task in self._tasks.values()
                        if task.status in {"completed", "failed", "cancelled"}
                    ),
                    key=lambda task: task.updated_at,
                )
                while len(self._tasks) >= MAX_STORED_AGENT_TASKS and finished:
                    self._tasks.pop(finished.pop(0).id, None)
                if len(self._tasks) >= MAX_STORED_AGENT_TASKS:
                    raise RuntimeError("Agent task capacity is full.")
            agent_task = AgentTask(
                id=str(uuid4()),
                project_id=project_id,
                user_id=user_id,
                task=task,
                current_context=current_context[:MAX_AGENT_CONTEXT_CHARS],
            )
            agent_task.add_step("analyze", "completed", "Task received")
            self._tasks[agent_task.id] = agent_task
            return agent_task

    def get(self, task_id: str, user_id: int) -> AgentTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.user_id != user_id:
                return None
            return task

    def transition(
        self,
        task_id: str,
        user_id: int,
        expected_status: str,
        next_status: str,
    ) -> AgentTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if (
                task is None
                or task.user_id != user_id
                or task.status != expected_status
            ):
                return None
            task.status = next_status
            task.updated_at = datetime.now(timezone.utc).isoformat()
            return task

    def cancel(self, task_id: str, user_id: int) -> AgentTask | None:
        with self._lock:
            task = self.get(task_id, user_id)
            if task is None:
                return None
            if task.status not in {"completed", "failed", "cancelled"}:
                task.status = "cancelled"
                task.stop_reason = "Cancelled by user."
                task.add_step("finish", "completed", task.stop_reason)
            return task


class TaskPlanner:
    @staticmethod
    def prompt(task: str) -> str:
        return (
            "Plan and propose a minimal implementation for this coding task. "
            "Use only the provided project context. Do not claim to have "
            "changed files. Return a single JSON object with keys: plan "
            "(array of at most 12 concise strings), actions (array of at most "
            "10 CodeAction objects using type=code_change and operation=replace), "
            "and validation_command (a string or null). Each action must use "
            "exact file_path, start_line, end_line, old_code, new_code, and "
            "description. Include old_code and copy it exactly from the "
            "inspected file, including whitespace and line breaks. Use an "
            "empty old_code only when replacing an actually empty file at line "
            "1. If exact source text is unavailable, return no actions. "
            "Propose commands only from pytest, python -m pytest, "
            "npm test, npm run build, npm run lint, git status, git diff. "
            "If there is insufficient context, return no actions and explain "
            "what must be inspected in the plan.\n\n"
            f"TASK:\n{task}"
        )

    @staticmethod
    def repair_prompt(task: str, response: str, error: str) -> str:
        return (
            "Your previous plan was invalid. Return a corrected single JSON "
            "object with the same required keys and constraints. Fix the "
            "validation issue without weakening the requirements. For replace "
            "actions, old_code must be included and copied exactly from the "
            "provided project context. Use an empty string only for an actually "
            "empty file at line 1. If exact source text is unavailable, return "
            "no actions and explain why in the plan.\n\n"
            f"VALIDATION ERROR:\n{error[:1000]}\n\n"
            f"TASK:\n{task}\n\n"
            f"INVALID RESPONSE:\n{response[:MAX_AGENT_OUTPUT_CHARS]}"
        )

    @staticmethod
    def parse(response: str) -> tuple[list[str], list[CodeAction], str | None]:
        candidates = [response]
        candidates.extend(
            match.group(1)
            for match in re.finditer(
                r"```(?:json)?\s*([\s\S]*?)```",
                response,
                flags=re.IGNORECASE,
            )
        )
        parsed: dict[str, object] | None = None
        for candidate in candidates:
            try:
                parsed_value = json.loads(candidate)
                if isinstance(parsed_value, dict):
                    parsed = cast(dict[str, object], parsed_value)
                break
            except (ValueError, TypeError):
                continue
        if not isinstance(parsed, dict):
            raise ValueError("The agent returned an invalid plan.")

        raw_plan_value = parsed.get("plan", [])
        raw_actions_value = parsed.get("actions", [])
        if not isinstance(raw_plan_value, list) or not isinstance(raw_actions_value, list):
            raise ValueError("The agent returned malformed plan fields.")
        raw_plan = cast(list[object], raw_plan_value)
        raw_actions = cast(list[object], raw_actions_value)
        if len(raw_plan) > MAX_AGENT_PLAN_STEPS or len(raw_actions) > MAX_AGENT_ACTIONS:
            raise ValueError("The agent plan exceeds the allowed size.")

        plan = [item[:500] for item in raw_plan if isinstance(item, str)]
        actions = [CodeAction.model_validate(item) for item in raw_actions]
        if any(action.operation != "replace" for action in actions):
            raise ValueError("Only validated replace actions are supported.")
        action_paths = [action.file_path for action in actions]
        if len(action_paths) != len(set(action_paths)):
            raise ValueError("Only one proposed action per file is supported.")

        command: object = parsed.get("validation_command")
        if command is not None:
            if not isinstance(command, str):
                raise ValueError("The validation command must be text.")
            validate_agent_command(command)
        return plan, actions, command


_SAFE_PYTEST_FLAGS = {"-q", "-v", "-x", "--tb=short", "--maxfail=1"}
_SAFE_TEST_PATH = re.compile(r"^(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+$")


def validate_agent_command(command: str) -> str:
    normalized = command.strip()
    if (
        not normalized
        or len(normalized) > 500
        or ".." in normalized
        or normalized.startswith(("/", "\\"))
        or re.search(r"(?:^|\s)[A-Za-z]:[/\\]|(?:^|\s)/|=[/\\]", normalized)
        or re.search(r"[;&|`$<>\n\r\\]", normalized)
    ):
        raise ValueError("This command is not allowed for agent validation.")
    tokens = normalized.split()
    pytest_prefix = ["pytest"]
    python_pytest_prefix = ["python", "-m", "pytest"]
    if tokens[:3] == python_pytest_prefix:
        args = tokens[3:]
    elif tokens[:1] == pytest_prefix:
        args = tokens[1:]
    elif normalized in {"npm test", "npm run build", "npm run lint", "git status", "git diff"}:
        return normalized
    else:
        raise ValueError("This command is not allowed for agent validation.")
    for token in args:
        if token in _SAFE_PYTEST_FLAGS:
            continue
        if token.startswith("-") or not _SAFE_TEST_PATH.fullmatch(token):
            raise ValueError("This command is not allowed for agent validation.")
    return normalized