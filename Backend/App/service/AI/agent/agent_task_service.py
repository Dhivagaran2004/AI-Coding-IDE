from typing import cast
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from App.config import AI_AGENT_MAX_CORRECTION_RETRIES, AI_AGENT_MAX_TOOL_CALLS
from App.models.agent_task_record import AgentTaskRecord
from App.schema.agent_schema import AgentPlanningResult, AgentPlan
from App.schema.ai_schema import CodeAction


MAX_AGENT_ITERATIONS = 5
MAX_AGENT_PLAN_STEPS = 12
MAX_AGENT_ACTIONS = 40
MAX_AGENT_CONTEXT_CHARS = 30000
MAX_AGENT_OUTPUT_CHARS = 12000
MAX_STORED_AGENT_TASKS = 200
ACTIVE_AGENT_TASK_STATUSES = frozenset({
    "pending",
    "planning",
    "executing",
    "validating",
})
WAITING_AGENT_TASK_STATUSES = frozenset({
    "awaiting_approval",
    "awaiting_clarification",
    "awaiting_validation",
    "paused",
})
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
    plan_details: dict[str, object] | None = None
    task_history: list[dict[str, object]] = field(default_factory=list)
    clarification_questions: list[str] = field(default_factory=list)
    clarification_history: list[dict[str, str]] = field(default_factory=list)
    actions: list[dict[str, object]] = field(
        default_factory=lambda: list[dict[str, object]]()
    )
    validation_command: str | None = None
    validation_result: dict[str, object] | None = None
    preview_url: str | None = None
    verification_status: str = "pending"
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

    def initialize_plan_task_history(self) -> None:
        if self.plan_details is None:
            return
        plan_id = str(self.plan_details.get("plan_id", ""))
        plan_tasks = self.plan_details.get("tasks", [])
        if not isinstance(plan_tasks, list):
            return
        for plan_task in plan_tasks:
            if not isinstance(plan_task, dict):
                continue
            plan_task_id = str(plan_task.get("id", ""))
            if not plan_task_id:
                continue
            if any(
                item.get("plan_id") == plan_id
                and item.get("iteration") == self.iteration
                and item.get("plan_task_id") == plan_task_id
                for item in self.task_history
            ):
                continue
            self.task_history.append({
                "plan_id": plan_id,
                "iteration": self.iteration,
                "plan_task_id": plan_task_id,
                "status": str(plan_task.get("status", "pending")),
                "description": "Plan task recorded.",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        self.task_history = self.task_history[-1000:]
        self.persist()

    def set_plan_task_status(
        self,
        plan_task_id: str,
        status: str,
        description: str,
        verification_notes: str | None = None,
    ) -> None:
        if self.plan_details is None:
            raise ValueError("The agent task has no structured plan.")
        plan_tasks = self.plan_details.get("tasks")
        if not isinstance(plan_tasks, list):
            raise ValueError("The structured plan has no task list.")
        plan_task = next(
            (
                item
                for item in plan_tasks
                if isinstance(item, dict) and item.get("id") == plan_task_id
            ),
            None,
        )
        if plan_task is None:
            raise ValueError(f"Unknown plan task: {plan_task_id}.")
        current_status = str(plan_task.get("status", "pending"))
        allowed_transitions = {
            "pending": {"in_progress", "completed", "blocked"},
            "in_progress": {"applied", "completed", "blocked"},
            "applied": {"completed", "blocked"},
            "completed": set(),
            "blocked": set(),
        }
        if status == current_status:
            return
        if status not in allowed_transitions.get(current_status, set()):
            raise ValueError(
                f"Plan task {plan_task_id} cannot transition from "
                f"{current_status} to {status}."
            )
        plan_task["status"] = status
        history_entry: dict[str, object] = {
            "plan_id": str(self.plan_details.get("plan_id", "")),
            "iteration": self.iteration,
            "plan_task_id": plan_task_id,
            "status": status,
            "description": description[:1000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if verification_notes:
            history_entry["verification_notes"] = verification_notes[:2000]
        self.task_history.append(history_entry)
        self.task_history = self.task_history[-1000:]
        self.add_step(
            "plan_task",
            status,
            f"{plan_task_id}: {description}"[:1000],
            output=(verification_notes or "")[:2000],
        )

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
            "plan_details": self.plan_details,
            "task_history": self.task_history[-100:],
            "clarification_questions": self.clarification_questions[:3],
            "actions": self.actions[:MAX_AGENT_ACTIONS],
            "validation_command": self.validation_command,
            "validation_result": self.validation_result,
            "preview_url": self.preview_url,
            "verification_status": self.verification_status,
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
        state["task_history"] = self.task_history
        return {
            **state,
            "user_id": self.user_id,
            "current_context": self.current_context,
            "clarification_history": self.clarification_history,
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
            plan_details=state.get("plan_details"),
            task_history=list(state.get("task_history", [])),
            clarification_questions=list(state.get("clarification_questions", [])),
            clarification_history=list(state.get("clarification_history", [])),
            actions=list(state.get("actions", [])),
            validation_command=state.get("validation_command"),
            validation_result=state.get("validation_result"),
            preview_url=(
                str(state["preview_url"])
                if state.get("preview_url") is not None
                else None
            ),
            verification_status=str(state.get("verification_status", "pending")),
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
    CHANGE_INTENT = re.compile(
        r"\b(add|build|change|create|delete|edit|fix|generate|implement|"
        r"make|modify|refactor|remove|update|write)\b",
        re.IGNORECASE,
    )

    @classmethod
    def request_expects_changes(cls, task: str) -> bool:
        return bool(cls.CHANGE_INTENT.search(task))

    @staticmethod
    def tool_prompt(
        task: str,
        history: list[dict[str, object]],
        clarifications: list[dict[str, str]] | None = None,
    ) -> str:
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
        clarification_context = ""
        if clarifications:
            clarification_context = (
                "\n\nUSER CLARIFICATIONS:\n"
                + json.dumps(clarifications[-10:], ensure_ascii=True)[:5000]
            )
        return (
            "You are the planning agent for this IDE. First understand the "
            "request and inspect repository context before proposing code. "
            "Use only the provided project context and validated tools. Treat "
            "REPOSITORY INTELLIGENCE as file-derived evidence: prefer manifests, "
            "README commands, test paths, entry points, and configuration files "
            "over guesses, and do not claim candidates were executed or verified. "
            "Return "
            "one JSON object per turn. To request a validated tool, "
            'use {"type":"tool_call","name":"...","arguments":{...}}. After '
            "receiving its result, request another tool or return a final "
            "structured planning result. Available tools: list_files(path?: string) lists "
            "safe files and directories; read_file(path: string) reads a safe "
            "project file; create_file(path, content), update_file(path, content), "
            "and delete_file(path) propose changes that require user approval; "
            "execute_command(command) proposes an allowlisted Docker command "
            "that requires user approval; get_execution_result() reads the "
            "latest sandbox result. File changes and commands are never applied "
            "without approval. Do not request secrets or environment variables. "
            "Keep calls relevant and bounded. "
            "The tool-call limit is "
            f"{AI_AGENT_MAX_TOOL_CALLS} total.\n\n"
            f"TASK:\n{task}\n\n"
            f"{clarification_context}\n\n"
            "PRIOR TOOL RESULTS:\n"
            f"{json.dumps(bounded_history, ensure_ascii=True)[:24000]}\n\n"
            "Return a final JSON object with status, plan, clarification_questions, "
            "actions, and validation_command. Use status=clarification_required "
            "and ask one to three focused questions if an unresolved choice "
            "materially changes the implementation; then plan must be null and "
            "actions and validation_command must be empty/null. Otherwise use "
            "status=ready and a structured plan. The plan keys are plan_id, "
            "objective, project_type, scope, project_understanding, assumptions, "
            "requirements, acceptance_criteria, technical_approach, tasks, risks, "
            "and validation_strategy. Each requirement has id, description, and "
            "category (functional, technical, quality, documentation, or analysis). "
            "Each task has id, task_type (implementation, analysis, verification, "
            "setup, or documentation), description, objective, dependencies, "
            "requirement_ids, expected_files, required_tools, acceptance_criteria, "
            "verification_steps, and status=pending. Do not supply system-managed "
            "acceptance_results or verification_status. Required tool names are "
            "list_files, read_file, create_file, update_file, delete_file, "
            "execute_command, and get_execution_result. Use at most 12 tasks, "
            "40 actions, and 3 clarification questions. Infer project type and "
            "treat user clarifications about phase ordering as binding. Never "
            "ask a clarification question solely because of the action limit: "
            "plan the first coherent phase with at most 40 actions and defer "
            "later phases instead of exceeding the limit. Do not include "
            "later-phase expected files or actions in the current phase. "
            "conventions from the repository when possible; state assumptions "
            "instead of inventing facts. For a new application, identify likely "
            "functional, technical, quality, and documentation requirements; do "
            "not assume an unspecified UI or persistence choice when it materially "
            "affects the architecture. Every requirement must map to a task. "
            "Every implementation task must list expected_files, "
            "acceptance_criteria, and verification_steps. Return a dependency-valid "
            "plan with an acceptance and validation strategy. Every field ending "
            "in _criteria, _steps, _strategy, _ids, _files, _tools, _questions, "
            "or _results that is defined as a list must be a JSON array, even if "
            "it contains only one item. In particular, acceptance_criteria and "
            "validation_strategy are arrays of strings. Functional and technical "
            "requirements must map to implementation tasks; quality requirements "
            "may map to implementation or verification tasks. Include exactly "
            "one action "
            "for every path in all implementation tasks' expected_files. Use no "
            "more than 40 actions. Use no extra keys. Treat user clarifications "
            "about phase ordering as binding, and plan only the first requested "
            "phase if the user has asked for phased work.\n\n"
            "For existing files use operation=replace exactly, with exact source "
            "text, line range, old_code, new_code, file_path, and description. "
            "Use operation=create_file only for a new file. Do not directly "
            "return delete actions. Do not claim changes were applied."
        )

    @staticmethod
    def include_clarifications(
        task: str,
        clarifications: list[dict[str, str]],
    ) -> str:
        if not clarifications:
            return task
        return (
            f"{task}\n\nUSER CLARIFICATIONS:\n"
            f"{json.dumps(clarifications[-10:], ensure_ascii=True)[:5000]}"
        )

    @staticmethod
    def is_action_limit_clarification(questions: list[str]) -> bool:
        if not questions:
            return False
        return all(
            "action" in question.casefold()
            and any(
                marker in question.casefold()
                for marker in ("limit", "maximum", "exceed", "more than")
            )
            for question in questions
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
            "Understand the user's requirements before generating code. Use "
            "repository conventions when they answer an unspecified choice; "
            "treat REPOSITORY INTELLIGENCE as evidence from safe project files, "
            "prefer manifest dependencies, README commands, tests, entry points, "
            "and configuration files over guesses, and do not claim candidate "
            "commands or entry points were executed or verified. If configured "
            "project metadata conflicts with source manifests, report the discrepancy "
            "and state which evidence informed the plan. "
            "Ask one to three focused clarification questions when an unresolved "
            "choice materially changes the architecture or user-visible behavior. "
            "For a new application, identify likely CRUD, persistence, search, "
            "validation, error-handling, testing, and documentation requirements "
            "as appropriate, without presenting assumptions as user requirements. "
            "Infer project type and scope from the supplied project context. "
            "Infer the relevant existing target files from the project file list "
            "and inspected source; do not require the user to name files when "
            "the targets are clear. "
            "Return exactly one JSON object with keys status, plan, "
            "clarification_questions, actions, and validation_command. For "
            "clarification_required, include only focused questions; plan must "
            "be null and actions empty. For ready, plan must be a structured "
            "AgentPlan with objective, project_type, scope, project_understanding, assumptions, "
            "requirements, acceptance_criteria, technical_approach, tasks, risks, "
            "and validation_strategy. Each task must include a unique id, task_type, "
            "description, objective, dependencies, requirement_ids, expected_files, "
            "required_tools, acceptance_criteria, verification_steps, and status. "
            "Set task status=pending; do not supply system-managed "
            "acceptance_results or verification_status. "
            "Every requirement must map to a task; functional and technical "
            "requirements must map to implementation tasks, while quality "
            "requirements may map to implementation or verification tasks. "
            "Never ask a clarification question solely because of the 40-action "
            "limit. If the full request is larger, plan the first coherent phase "
            "with no more than 40 actions and defer later-phase files. Honor any "
            "user clarification about phase ordering. Every "
            "implementation task must name the exact files it will change and "
            "have acceptance criteria and verification steps. Ensure IDs and "
            "dependencies are valid and acyclic. Use one action per changed file "
            "and make action paths match the implementation tasks' expected_files. "
            "For multi-file work, propose coordinated actions for every necessary "
            "file, with one separate action per changed file, and describe how "
            "supporting files connect to the main/entry-point file. Do not return "
            "an empty actions array for an implementation request when relevant "
            "source is available. "
            "Do not claim to have changed files.\n\n"
            "For an existing file use operation=replace exactly with type="
            "code_change, file_path, description, 1-based start_line/end_line, "
            "old_code copied exactly from inspected source, and new_code. Use "
            "operation=create_file only for a new file, with its complete "
            "contents in new_code; do not use a top-level type=create_file or "
            "a content field. Do not invent file paths or old_code. Use an "
            "empty old_code only for an actually empty file at line 1. Do not "
            "return delete actions. Propose validation commands only from pytest, "
            "python -m pytest, python <project-local-script.py>, npm test, "
            "npm run build, npm run lint, git status, or git diff. If project "
            "context is insufficient, use read-only tools or ask a question.\n\n"
            f"TASK:\n{task}"
        )

    @staticmethod
    def repair_prompt(task: str, response: str, error: str) -> str:
        return (
            "Your previous planning result was invalid. Return a corrected "
            "single JSON object using the exact structured result schema from "
            "the original request. Fix every reported issue; do not omit invalid "
            "actions, silently drop requirements, or weaken acceptance criteria. "
            "The action limit is an internal constraint, not a user decision; "
            "never return clarification_required just to ask about action limits. "
            "All schema fields defined as lists must be JSON arrays, including "
            "acceptance_criteria, verification_steps, validation_strategy, "
            "expected_files, requirement_ids, and clarification_questions. "
            "Include exactly one action for every expected file path. Map "
            "functional and technical requirement IDs to implementation tasks "
            "and quality requirements to implementation or verification tasks. "
            "The result may contain up to 40 actions. Honor user clarifications "
            "about phase ordering and do not ask solely about the action limit. "
            "Preserve all requirements, task dependencies, expected files, "
            "acceptance criteria, and verification steps. For existing files, "
            "copy old_code exactly from the provided project context. Use "
            "operation=create_file with new_code only for a genuinely new file; "
            "do not use a top-level type=create_file or a content field. "
            "Do not invent source text or file paths.\n\n"
            f"VALIDATION ERROR:\n{error[:4000]}\n\n"
            f"TASK:\n{task}\n\n"
            f"INVALID RESPONSE:\n{response[:MAX_AGENT_OUTPUT_CHARS]}"
        )

    @staticmethod
    def sandbox_diagnosis_prompt(
        task: str,
        command: str,
        stdout: str,
        stderr: str,
        attempt: int,
    ) -> str:
        return (
            "Analyze the failed command from an isolated, offline project sandbox. "
            "Return one JSON object with exactly three keys: issues (array of "
            "strings), root_causes (array of strings), and fix_plan (array of "
            "strings). Cite evidence from the output, distinguish missing "
            "dependencies/environment blockers from source-code defects, and "
            "recommend only bounded changes relevant to the user's task. Do not "
            "claim that any fix has been applied or verified.\n\n"
            f"ATTEMPT: {attempt}\n"
            f"TASK:\n{task[:5000]}\n\n"
            f"COMMAND:\n{command}\n\n"
            f"EXIT OUTPUT:\n{stdout[:6000]}\n\n"
            f"ERROR OUTPUT:\n{stderr[:6000]}"
        )

    @staticmethod
    def correction_repair_prompt(
        task: str,
        response: str,
        error: str,
        validation_result: dict[str, object],
        completed_work: str,
    ) -> str:
        return (
            "Your previous correction proposal was invalid. Return one corrected "
            "structured planning result that addresses the reported validation "
            "failure. Preserve the user's requirements and completed work; do not "
            "repeat completed tasks or propose unrelated changes. Include only "
            "the minimal necessary correction actions and a validation command "
            "when one is appropriate. Keep every mutation as a proposal requiring "
            "user approval; do not claim changes were applied or verified. For "
            "existing files, copy exact source text from the supplied project "
            "context. Do not invent file paths, source text, or successful results.\n\n"
            f"VALIDATION FAILURE:\n"
            f"{json.dumps(validation_result, ensure_ascii=True)[:12000]}\n\n"
            f"COMPLETED WORK TO PRESERVE:\n{completed_work[:8000]}\n\n"
            f"VALIDATION ERROR:\n{error[:1000]}\n\n"
            f"TASK:\n{task}\n\n"
            f"INVALID CORRECTION PROPOSAL:\n{response[:MAX_AGENT_OUTPUT_CHARS]}"
        )

    @staticmethod
    def missing_actions_prompt(task: str, response: str) -> str:
        return (
            "Your previous ready plan describes implementation work but "
            "does not include the matching complete set of file actions. Return "
            "a corrected structured planning result with all requirement/task "
            "mappings preserved. infer the relevant existing target files from "
            "the supplied project context, then include exactly one action for every expected "
            "changed file in implementation tasks. Use operation=replace with "
            "exact source text for existing files and operation=create_file "
            "with new_code for new files. Do not invent file paths or old_code. "
            "All list fields must be JSON arrays, and the result may contain up "
            "to 40 actions. "
            "If the work is actually ambiguous, return clarification_required "
            "with one to three focused questions and no plan or actions.\n\n"
            f"TASK:\n{task}\n\n"
            f"PREVIOUS RESPONSE:\n{response[:MAX_AGENT_OUTPUT_CHARS]}"
        )

    @staticmethod
    def _parse_json_object(response: str) -> dict[str, object]:
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
                parsed_value = json.loads(candidate)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed_value, dict):
                return cast(dict[str, object], parsed_value)
        raise ValueError("The agent returned an invalid planning result.")

    @staticmethod
    def parse_planning_result(
        response: str,
        existing_actions: list[CodeAction] | None = None,
    ) -> AgentPlanningResult:
        payload = TaskPlanner._parse_json_object(response)
        if "actions" not in payload:
            raise ValueError("The planning result must include an actions array.")
        raw_actions = payload.get("actions", [])
        if not isinstance(raw_actions, list):
            raise ValueError("The agent returned malformed actions.")
        if len(raw_actions) > MAX_AGENT_ACTIONS:
            raise ValueError(
                f"The agent returned {len(raw_actions)} actions; "
                f"the current limit is {MAX_AGENT_ACTIONS}."
            )

        payload["actions"] = [
            TaskPlanner._parse_action(item).model_dump()
            for item in raw_actions
        ]
        result = AgentPlanningResult.model_validate(payload)
        if result.plan is not None and any(
            task.status != "pending"
            or task.verification_status != "pending"
            or task.acceptance_results
            for task in result.plan.tasks
        ):
            raise ValueError("New plan tasks must start with pending status.")
        if result.status == "clarification_required":
            return result
        if any(
            action.operation not in {"replace", "create_file"}
            for action in result.actions
        ):
            raise ValueError(
                "Only validated replace and create_file actions are supported."
            )

        command = result.validation_command
        if command is not None:
            try:
                result.validation_command = validate_agent_command(command)
            except ValueError as error:
                raise ValueError(
                    "The planning result contains a disallowed validation command."
                ) from error

        action_paths = [action.file_path for action in result.actions]
        if len(action_paths) != len(set(action_paths)):
            raise ValueError("Only one proposed action per file is supported.")
        expected_paths = {
            path
            for task in cast(AgentPlan, result.plan).tasks
            for path in task.expected_files
        }
        existing_paths = {
            action.file_path
            for action in (existing_actions or [])
        }
        proposed_paths = set(action_paths) | existing_paths
        missing = sorted(expected_paths - proposed_paths)
        unexpected = sorted(set(action_paths) - expected_paths)
        if missing or unexpected:
            details = []
            if missing:
                details.append(f"missing actions for expected files: {', '.join(missing)}")
            if unexpected:
                details.append(f"actions not declared by implementation tasks: {', '.join(unexpected)}")
            raise ValueError("Plan/action coverage mismatch (" + "; ".join(details) + ").")
        return result

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
            raise ValueError(
                f"The agent plan exceeds the allowed size "
                f"({len(raw_plan)} steps, {len(raw_actions)} actions; limits are "
                f"{MAX_AGENT_PLAN_STEPS} steps and {MAX_AGENT_ACTIONS} actions)."
            )

        if any(not isinstance(item, str) or not item.strip() for item in raw_plan):
            raise ValueError("The agent plan must contain non-empty text steps.")
        plan = [cast(str, item)[:500] for item in raw_plan]
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
            command = validate_agent_command(command)
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
    if normalized == "python -m compileall .":
        return normalized
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