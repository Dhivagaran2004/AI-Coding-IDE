from typing import cast
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from App.config import AI_AGENT_MAX_TOOL_CALLS
from App.models.agent_task_record import AgentTaskRecord
from App.schema.ai_schema import CodeAction


MAX_AGENT_ITERATIONS = 5
MAX_AGENT_PLAN_STEPS = 12
MAX_AGENT_ACTIONS = 10
MAX_AGENT_CONTEXT_CHARS = 30000
MAX_AGENT_OUTPUT_CHARS = 12000
MAX_STORED_AGENT_TASKS = 200
ACTIVE_AGENT_TASK_STATUSES = frozenset({
    "pending",
    "planning",
    "executing",
    "validating",
})
WAITING_AGENT_TASK_STATUSES = frozenset({"awaiting_approval", "paused"})
TERMINAL_AGENT_TASK_STATUSES = frozenset({
    "completed",
    "failed",
    "cancelled",
})


class AgentToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^tool_call$")
    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, object]


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
    changes: list[dict[str, object]] = field(
        default_factory=lambda: list[dict[str, object]]()
    )
    tool_history: list[dict[str, object]] = field(
        default_factory=lambda: list[dict[str, object]]()
    )
    tool_calls_used: int = 0
    tool_protocol_active: bool = False
    steps: list[AgentStep] = field(default_factory=lambda: list[AgentStep]())
    stop_reason: str | None = None
    iteration: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    on_change: Callable[["AgentTask"], None] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def add_step(
        self,
        step_type: str,
        status: str,
        description: str,
        **values: str,
    ) -> AgentStep:
        step = AgentStep(
            sequence=len(self.steps) + 1,
            type=step_type,
            status=status,
            description=description,
            **values,
        )
        self.steps.append(step)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.persist()
        return step

    def persist(self) -> None:
        if self.on_change is not None:
            self.on_change(self)

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
            "changes": self.changes[-20:],
            "tool_calls_used": self.tool_calls_used,
            "iteration": self.iteration,
            "steps": [step.as_dict() for step in self.steps[-50:]],
            "stop_reason": self.stop_reason,
        }

    def as_storage(self) -> dict[str, object]:
        state = self.as_dict()
        state["change_history"] = self.change_history
        state["changes"] = self.changes
        return {
            **state,
            "user_id": self.user_id,
            "current_context": self.current_context,
            "tool_history": self.tool_history,
            "tool_calls_used": self.tool_calls_used,
            "tool_protocol_active": self.tool_protocol_active,
        }


class AgentTaskStore:
    """In-memory task cache backed by an optional durable database store."""

    def __init__(self, session_factory=None):
        self._tasks: dict[str, AgentTask] = {}
        self._lock = RLock()
        self._session_factory = session_factory
        self._active_workers: set[str] = set()

    @staticmethod
    def _restore(record: AgentTaskRecord) -> AgentTask:
        state = record.state
        steps = [
            AgentStep(
                sequence=int(step["sequence"]),
                type=str(step["type"]),
                status=str(step["status"]),
                description=str(step["description"]),
                input=str(step.get("input", "")),
                output=str(step.get("output", "")),
                error=(
                    str(step["error"])
                    if step.get("error") is not None
                    else None
                ),
                created_at=str(step["created_at"]),
            )
            for step in state.get("steps", [])
        ]
        task = AgentTask(
            id=record.id,
            project_id=record.project_id,
            user_id=record.user_id,
            task=record.task,
            current_context=str(state.get("current_context", "")),
            status=record.status,
            plan=list(state.get("plan", [])),
            actions=list(state.get("actions", [])),
            validation_command=state.get("validation_command"),
            validation_result=state.get("validation_result"),
            change_history=list(state.get("change_history", [])),
            changes=list(state.get("changes", [])),
            tool_history=list(state.get("tool_history", [])),
            tool_calls_used=int(state.get("tool_calls_used", 0)),
            tool_protocol_active=bool(state.get("tool_protocol_active", False)),
            steps=steps,
            stop_reason=state.get("stop_reason"),
            iteration=int(state.get("iteration", 0)),
            created_at=str(state.get("created_at", record.created_at.isoformat())),
            updated_at=str(state.get("updated_at", record.updated_at.isoformat())),
        )
        if task.status in ACTIVE_AGENT_TASK_STATUSES:
            task.status = "paused"
            task.stop_reason = (
                "Recovered after a server restart. Review completed steps before continuing."
            )
        return task

    def save(self, task: AgentTask) -> None:
        if self._session_factory is None:
            return
        db = self._session_factory()
        try:
            task.updated_at = datetime.now(timezone.utc).isoformat()
            record = db.get(AgentTaskRecord, task.id)
            if record is None:
                record = AgentTaskRecord(
                    id=task.id,
                    project_id=task.project_id,
                    user_id=task.user_id,
                    task=task.task,
                    status=task.status,
                    state=task.as_storage(),
                )
                db.add(record)
            else:
                record.project_id = task.project_id
                record.user_id = task.user_id
                record.task = task.task
                record.status = task.status
                record.state = task.as_storage()
                record.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _load(self, task_id: str, user_id: int) -> AgentTask | None:
        if self._session_factory is None:
            return None
        db = self._session_factory()
        try:
            record = (
                db.query(AgentTaskRecord)
                .filter(
                    AgentTaskRecord.id == task_id,
                    AgentTaskRecord.user_id == user_id,
                )
                .first()
            )
            if record is None:
                return None
            task = self._restore(record)
        finally:
            db.close()
        task.on_change = self.save
        with self._lock:
            task = self._tasks.setdefault(task_id, task)
        if task.status == "paused" and record.status != "paused":
            self.save(task)
        return task

    def list_for_user(
        self,
        user_id: int,
        project_id: int | None = None,
    ) -> list[AgentTask]:
        if self._session_factory is None:
            with self._lock:
                return [
                    task for task in self._tasks.values()
                    if task.user_id == user_id
                    and (project_id is None or task.project_id == project_id)
                ]
        db = self._session_factory()
        try:
            query = db.query(AgentTaskRecord).filter(
                AgentTaskRecord.user_id == user_id
            )
            if project_id is not None:
                query = query.filter(AgentTaskRecord.project_id == project_id)
            records = query.order_by(AgentTaskRecord.updated_at.desc()).limit(
                MAX_STORED_AGENT_TASKS
            ).all()
            tasks = [self._restore(record) for record in records]
        finally:
            db.close()
        with self._lock:
            for task in tasks:
                task.on_change = self.save
                self._tasks[task.id] = task
        for task, record in zip(tasks, records):
            if task.status == "paused" and record.status != "paused":
                self.save(task)
        return tasks

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
                on_change=self.save,
            )
            agent_task.add_step("analyze", "completed", "Task received")
            self._tasks[agent_task.id] = agent_task
            self.save(agent_task)
            return agent_task

    def get(self, task_id: str, user_id: int) -> AgentTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                return task if task.user_id == user_id else None
        return self._load(task_id, user_id)

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
            task.persist()
            return task

    def claim_worker(self, task_id: str, user_id: int) -> AgentTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if (
                task is None
                or task.user_id != user_id
                or task_id in self._active_workers
                or task.status not in {"pending", "planning"}
            ):
                return None
            self._active_workers.add(task_id)
            return task

    def release_worker(self, task_id: str) -> None:
        with self._lock:
            self._active_workers.discard(task_id)

    def cancel(self, task_id: str, user_id: int) -> AgentTask | None:
        with self._lock:
            task = self.get(task_id, user_id)
            if task is None:
                return None
            if task.status not in TERMINAL_AGENT_TASK_STATUSES:
                task.status = "cancelled"
                task.stop_reason = "Cancelled by user."
                task.add_step("finish", "completed", task.stop_reason)
            return task


class TaskPlanner:
    @staticmethod
    def tool_prompt(task: str, history: list[dict[str, object]]) -> str:
        bounded_history: list[dict[str, object]] = []
        for entry in history[-5:]:
            bounded_entry = dict(entry)
            result = bounded_entry.get("result")
            if isinstance(result, dict):
                bounded_result = dict(result)
                content = bounded_result.get("content")
                if isinstance(content, str) and len(content) > 4000:
                    bounded_result["content"] = content[:4000]
                    bounded_result["truncated"] = True
                bounded_entry["result"] = bounded_result
            bounded_history.append(bounded_entry)
        return (
            "You are the tool-using coding agent for this IDE. Use only the "
            "provided project context and the validated tools below. Return "
            "one JSON object per turn. To request a tool, use "
            '{"type":"tool_call","name":"...","arguments":{...}}. After '
            "receiving its result, request another tool or return a final "
            "implementation plan using the plan/actions/validation_command "
            "JSON schema. Available tools: list_files(path?: string) lists "
            "safe files and directories; read_file(path: string) reads a safe "
            "project file; create_file(path: string, content: string) proposes "
            "a new file for approval; update_file(path: string, content: string) "
            "proposes a replacement for approval; delete_file(path: string) "
            "proposes deletion for explicit approval; execute_command(command: "
            "string) proposes a restricted Docker command for explicit approval; "
            "get_execution_result() returns the latest sandbox result. Read "
            "files before proposing edits. File mutations and commands are never "
            "executed until the user approves them. Commands are restricted to "
            "the existing agent allowlist. Do not request secrets or environment "
            "variables. Keep calls relevant and bounded. The tool-call limit is "
            f"{AI_AGENT_MAX_TOOL_CALLS} total.\n\n"
            f"TASK:\n{task}\n\n"
            "PRIOR TOOL RESULTS:\n"
            f"{json.dumps(bounded_history, ensure_ascii=True)[:24000]}\n\n"
            "If no tools are needed, return the final plan directly as one JSON "
            "object with keys plan (up to 12 strings), actions (up to 10 "
            "code_change objects), and validation_command (string or null). "
            "For existing files use operation=replace exactly, with exact source text, "
            "line range, old_code, new_code, file_path, and description. Use "
            "operation=create_file only for a new file. Do not directly return "
            "delete actions; request delete_file so the backend can require "
            "confirmation. Do not directly claim changes were applied."
        )

    @staticmethod
    def parse_tool_call(response: str) -> AgentToolCall | None:
        candidates = [response]
        candidates.extend(
            match.group(1)
            for match in re.finditer(
                r"```(?:json)?\s*([\s\S]*?)```",
                response,
                flags=re.IGNORECASE,
            )
        )
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and payload.get("type") == "tool_call":
                try:
                    return AgentToolCall.model_validate(payload)
                except ValueError as error:
                    raise ValueError("The agent returned a malformed tool call.") from error
        return None

    @staticmethod
    def prompt(task: str) -> str:
        return (
            "Implement this coding task with the smallest complete set of changes. "
            "Use only the provided project context. Do not claim to have "
            "changed files. Infer the relevant existing target files from the "
            "project file list and inspected source; do not require the user to "
            "name files when the targets are clear. For a feature spanning "
            "multiple files, propose coordinated actions for every necessary "
            "file, with exactly one separate action per changed file. Use "
            "detected file relationships to include supporting modules and "
            "wire them to the main/entry-point file with the required imports "
            "or calls; describe those links in the plan. If the task requires "
            "a truly new file, use operation=create_file and include a valid new "
            "file path; otherwise prefer operation=replace. Do not return an "
            "empty actions array for an implementation request when the relevant "
            "source is available in context. Return no actions only when no "
            "appropriate existing file can be identified or its exact source "
            "is unavailable. Return a single JSON object with keys: plan "
            "(array of at most 12 concise strings), actions (array of at most "
            "10 CodeAction objects using type=code_change and operation=replace "
            "or create_file), and validation_command (a string or null). Use "
            "operation=replace exactly for existing files; do not use create, "
            "insert, or delete. Use operation=create_file only when the file is "
            "actually new. Each replace action must use exact file_path, "
            "start_line, end_line, old_code, new_code, and description. Include "
            "old_code and copy it exactly from the inspected file, including "
            "whitespace and line breaks. Use an empty old_code only when "
            "replacing an actually empty file at line 1. Never invent old_code "
            "or file paths. Propose commands only from pytest, python -m pytest, "
            "npm test, npm run build, npm run lint, git status, git diff. "
            "If there is insufficient context to safely propose changes, return "
            "no actions and explain what must be inspected in the plan.\n\n"
            f"TASK:\n{task}"
        )

    @staticmethod
    def repair_prompt(task: str, response: str, error: str) -> str:
        return (
            "Your previous plan was invalid. Return a corrected single JSON "
            "object with the same required keys and constraints. Fix the "
            "validation issue without weakening the requirements. Infer "
            "relevant existing target files from the project context; include "
            "coordinated actions for all necessary files when the task spans "
            "multiple files, with one separate action per changed file. "
            "Preserve required imports/calls between supporting files and the "
            "main/entry-point file, and describe those relationships in the "
            "plan. For existing files, use operation=replace exactly, with "
            "old_code copied exactly from the provided project context. Use "
            "operation=create_file with new_code for files that do not exist; "
            "when returning a create_file action, do not use a top-level "
            "type=create_file or a content field. Use an empty old_code only "
            "for an actually empty existing file at line 1. Do not invent "
            "source text or file paths.\n\n"
            f"VALIDATION ERROR:\n{error[:1000]}\n\n"
            f"TASK:\n{task}\n\n"
            f"INVALID RESPONSE:\n{response[:MAX_AGENT_OUTPUT_CHARS]}"
        )

    @staticmethod
    def missing_actions_prompt(task: str, response: str) -> str:
        return (
            "Your previous response did not propose any file changes. This is "
            "an implementation task: inspect the provided project file list "
            "and source context, infer the relevant existing target files, "
            "and return coordinated actions for the complete change. Use "
            "operation=replace with exact source text for existing files and "
            "operation=create_file with new_code for files that do not exist. "
            "Return one separate action for each changed file. When the task "
            "uses supporting files, wire them to the main/entry-point file "
            "with the required imports or calls and mention those links in "
            "the plan. Do not ask the user to identify files when the context "
            "makes suitable targets clear. Copy each old_code exactly from the "
            "provided source; do not invent file paths or source text. If no "
            "appropriate existing file or exact source is available, return no "
            "actions and explain the specific blocker in the plan. Return the "
            "same JSON structure and obey all original constraints.\n\n"
            f"TASK:\n{task}\n\n"
            f"PREVIOUS RESPONSE:\n{response[:MAX_AGENT_OUTPUT_CHARS]}"
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
        actions = [
            TaskPlanner._parse_action(item)
            for item in raw_actions
        ]
        supported_operations = {"replace", "create_file"}
        if any(action.operation not in supported_operations for action in actions):
            raise ValueError("Only validated replace and create_file actions are supported.")
        action_paths = [action.file_path for action in actions]
        if len(action_paths) != len(set(action_paths)):
            raise ValueError("Only one proposed action per file is supported.")

        command: object = parsed.get("validation_command")
        if command is not None:
            if not isinstance(command, str):
                raise ValueError("The validation command must be text.")
            try:
                command = validate_agent_command(command)
            except ValueError:
                command = None
        return plan, actions, command

    @staticmethod
    def _parse_action(value: object) -> CodeAction:
        if not isinstance(value, dict):
            return CodeAction.model_validate(value)
        action = dict(value)
        if action.get("type") == "create_file":
            action["type"] = "code_change"
            action.setdefault("operation", "create_file")
        if action.get("operation") == "create_file":
            if "content" in action:
                content = action.pop("content")
                if "new_code" in action and action["new_code"] != content:
                    raise ValueError(
                        "create_file action cannot contain conflicting content and new_code."
                    )
                action["new_code"] = content
        return CodeAction.model_validate(action)


_SAFE_PYTEST_FLAGS = {"-q", "-v", "-x", "--tb=short", "--maxfail=1"}
_SAFE_TEST_PATH = re.compile(r"^(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+$")
_SAFE_PYTHON_SCRIPT = re.compile(r"^(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py$")


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
    elif (
        len(tokens) == 2
        and tokens[0] in {"python", "python3"}
        and _SAFE_PYTHON_SCRIPT.fullmatch(tokens[1])
    ):
        return normalized
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