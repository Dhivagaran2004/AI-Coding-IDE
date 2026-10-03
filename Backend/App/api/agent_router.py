import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import cast

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from App.api.ai_router import ai_service
from App.api.terminal_router import terminal_service
from App.auth.auth import get_current_user
from App.database.database import SessionLocal, get_db
from App.models.project import Project
from App.models.user import User
from App.schema.agent_schema import (
    AgentApproval,
    AgentCommandApproval,
    AgentTaskCreate,
    AgentTaskResponse,
)
from App.schema.ai_schema import CodeAction, TerminalContext
from App.service.AI.agent.agent_task_service import (
    MAX_AGENT_ITERATIONS,
    AgentTask,
    AgentTaskStore,
    TaskPlanner,
    validate_agent_command,
)
from App.service.AI.agent.agent_tools import AgentToolError, AgentTools
from App.service.AI.code_action_service import (
    CodeActionService,
    PatchAction,
    PatchNotFoundError,
    PatchValidationError,
    StalePatchError,
    UnsupportedPatchOperation,
)


router = APIRouter(prefix="/ai/agent", tags=["AI Agent"])
logger = logging.getLogger(__name__)
agent_tasks = AgentTaskStore()


def _get_owned_task(task_id: str, user_id: int, db: Session) -> AgentTask:
    task = agent_tasks.get(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Agent task not found.")
    project = (
        db.query(Project)
        .filter(Project.id == task.project_id, Project.user_id == user_id)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return task


async def _plan_task(task_id: str, user_id: int) -> None:
    task = agent_tasks.get(task_id, user_id)
    if task is None or getattr(task, "status", None) == "cancelled":
        return
    task.status = "planning"
    task.add_step("retrieve", "running", "Retrieving project context")
    db = SessionLocal()
    try:
        tools = AgentTools(db, task.project_id, user_id)
        file_paths = tools.list_project_files()[:100]
        matches = tools.search_project(task.task)[:8]
        task.add_step("search", "completed", "Searched project files", output="\n".join(
            str(item.get("path", "")) for item in matches
        ))
        inspected: list[str] = []
        for result in matches[:4]:
            path = str(result["path"])
            try:
                file_data = tools.inspect_file(path)
            except AgentToolError:
                continue
            inspected.append(f"FILE: {path}\n{str(file_data['content'])[:6000]}")
            task.add_step("inspect", "completed", f"Inspected {path}")
        context = tools.retrieve_context(task.task)[:30000]
        tree = "PROJECT FILES:\n" + "\n".join(file_paths)
        if tree:
            context = f"{tree}\n\n{context}"
        if inspected:
            context = f"INSPECTED FILES:\n{'\n\n'.join(inspected)}\n\n{context}"
        if task.current_context:
            context = f"CURRENT IDE CONTEXT:\n{task.current_context}\n\n{context}"
        context = context[:30000]
        task.add_step(
            "retrieve", "completed", "Retrieved relevant project context",
            output=context[:12000],
        )
        if getattr(task, "status", None) == "cancelled":
            return
        response = await ai_service.chat(
            message=TaskPlanner.prompt(task.task),
            context=context or "No relevant repository context was found.",
            history=[],
        )
        if getattr(task, "status", None) == "cancelled":
            return
        try:
            plan, actions, command = TaskPlanner.parse(response)
        except ValueError as error:
            task.add_step(
                "plan", "running", "Repairing invalid implementation plan",
                error=str(error),
            )
            if getattr(task, "status", None) == "cancelled":
                return
            response = await ai_service.chat(
                message=TaskPlanner.repair_prompt(
                    task.task, response, str(error)
                ),
                context=context or "No relevant repository context was found.",
                history=[],
            )
            if getattr(task, "status", None) == "cancelled":
                return
            plan, actions, command = TaskPlanner.parse(response)
        for action in actions:
            target = tools.resolve_file(action.file_path)
            if tools.get_file_path(target) != action.file_path:
                raise ValueError("Agent action path does not match the project file.")
        task.plan = plan
        task.actions = []
        for action in actions:
            project_file = tools.resolve_file(action.file_path)
            task.actions.append({
                "action": action.model_dump(),
                "status": "awaiting_approval",
                "error": None,
            })
        task.validation_command = command
        task.add_step("plan", "completed", "Implementation plan prepared", output="\n".join(plan))
        if actions:
            task.add_step("code_action", "completed", f"Prepared {len(actions)} change(s) for review")
            task.status = "awaiting_approval"
        elif command:
            task.status = "awaiting_approval"
        else:
            task.status = "completed"
            task.stop_reason = "Planning completed without proposed file changes."
    except Exception as error:
        logger.exception("Agent planning failed for task %s", task_id)
        task.status = "failed"
        task.stop_reason = str(error)[:1000]
        task.add_step("finish", "failed", "Agent planning failed", error=str(error))
    finally:
        db.close()


@router.post("/tasks", status_code=202, response_model=AgentTaskResponse)
def create_agent_task(
    request: AgentTaskCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = (
        db.query(Project)
        .filter(Project.id == request.project_id, Project.user_id == current_user.id)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    user_id = cast(int, current_user.id)
    current_context_parts: list[str] = []
    current_file_is_safe = (
        request.current_file_path is None
        or AgentTools.is_safe_path(request.current_file_path)
    )
    if (
        request.context
        and current_file_is_safe
        and not AgentTools.SECRET_CONTENT.search(request.context)
    ):
        current_context_parts.append(request.context[:20000])
    if request.selected_code:
        selected = request.selected_code
        if (
            AgentTools.is_safe_path(selected.file_path)
            and not AgentTools.SECRET_CONTENT.search(selected.code)
        ):
            current_context_parts.append(
                f"SELECTED CODE: {selected.file_path}:{selected.start_line}-{selected.end_line}\n"
                f"```{selected.language}\n{selected.code[:12000]}\n```"
            )
    if request.terminal_context:
        terminal = request.terminal_context
        current_context_parts.append(
            f"TERMINAL OUTPUT ({terminal.exit_code}): {terminal.command}\n"
            f"STDOUT:\n{terminal.stdout[:6000]}\nSTDERR:\n{terminal.stderr[:6000]}"
        )
    current_context = "\n\n".join(current_context_parts)[:30000]
    task = agent_tasks.create(
        request.project_id,
        user_id,
        request.task.strip(),
        current_context=current_context,
    )
    background_tasks.add_task(_plan_task, task.id, user_id)
    return task.as_dict()


@router.get("/tasks/{task_id}", response_model=AgentTaskResponse)
def get_agent_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_owned_task(task_id, cast(int, current_user.id), db).as_dict()


@router.post("/tasks/{task_id}/approve", response_model=AgentTaskResponse)
def approve_agent_changes(
    task_id: str,
    request: AgentApproval,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = cast(int, current_user.id)
    task = _get_owned_task(task_id, user_id, db)
    if task.status != "awaiting_approval":
        raise HTTPException(status_code=409, detail="Task is not awaiting approval.")
    awaiting = [
        index for index, item in enumerate(task.actions)
        if item.get("status") == "awaiting_approval"
    ]
    selected = awaiting if request.accept_all else request.action_indexes
    rejected = awaiting if request.reject_all else request.reject_indexes
    if (
        any(index not in awaiting for index in selected + rejected)
        or len(selected) != len(set(selected))
        or len(rejected) != len(set(rejected))
    ):
        raise HTTPException(status_code=400, detail="Invalid change approval index.")
    if set(selected) & set(rejected):
        raise HTTPException(status_code=400, detail="A change cannot be accepted and rejected together.")
    if not selected:
        if rejected:
            if agent_tasks.transition(task.id, user_id, "awaiting_approval", "executing") is None:
                raise HTTPException(status_code=409, detail="Task approval is already being processed.")
            for index in rejected:
                task.actions[index]["status"] = "rejected"
            task.status = "awaiting_approval"
            task.add_step("code_action", "completed", "Changes rejected by user")
        if not task.validation_command and not any(
            item.get("status") == "awaiting_approval" for item in task.actions
        ):
            task.status = "completed"
            task.stop_reason = "All proposed changes were rejected."
            task.add_step("finish", "completed", task.stop_reason)
        return task.as_dict()

    if agent_tasks.transition(task.id, user_id, "awaiting_approval", "executing") is None:
        raise HTTPException(status_code=409, detail="Task approval is already being processed.")
    for index in rejected:
        task.actions[index]["status"] = "rejected"
    task.add_step("code_action", "running", "Applying approved changes")
    tools = AgentTools(db, task.project_id, user_id)
    change_results: list[dict[str, object]] = []
    for index in selected:
        if getattr(task, "status", None) == "cancelled":
            break
        item = task.actions[index]
        action = CodeAction.model_validate(item["action"])
        try:
            project_file = tools.resolve_file(action.file_path)
            old_content = project_file.content or ""
            updated_file = CodeActionService(db).apply_action(
                project_id=task.project_id,
                file_id=cast(int, project_file.id),
                user_id=user_id,
                action=cast(PatchAction, action),
            )
            item["status"] = "applied"
            task.change_history.append({
                "path": action.file_path,
                "file_id": updated_file.id,
                "old_hash": hashlib.sha256(old_content.encode("utf-8")).hexdigest(),
                "new_hash": hashlib.sha256((updated_file.content or "").encode("utf-8")).hexdigest(),
                "old_code": action.old_code,
                "new_code": action.new_code,
                "approved_by": user_id,
                "approved_at": datetime.now(timezone.utc).isoformat(),
            })
            change_results.append({
                "path": action.file_path,
                "content": updated_file.content or "",
                "file_id": updated_file.id,
            })
        except (
            AgentToolError, PatchNotFoundError, PatchValidationError,
            StalePatchError, UnsupportedPatchOperation,
        ) as error:
            item["status"] = "failed"
            item["error"] = str(error)
            task.status = "failed"
            task.stop_reason = str(error)
            task.add_step("code_action", "failed", "Approved change was rejected", error=str(error))
            break

    if task.status not in {"failed", "cancelled"}:
        pending = any(item.get("status") == "awaiting_approval" for item in task.actions)
        if pending or task.validation_command:
            task.status = "awaiting_approval"
        else:
            task.status = "completed"
            task.add_step("finish", "completed", "Approved changes applied")
    response: dict[str, object] = task.as_dict()
    response["changes"] = change_results
    return response


@router.post("/tasks/{task_id}/validate", response_model=AgentTaskResponse)
def run_agent_validation(
    task_id: str,
    request: AgentCommandApproval,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = cast(int, current_user.id)
    task = _get_owned_task(task_id, user_id, db)
    if task.status != "awaiting_approval" or task.validation_command is None:
        raise HTTPException(status_code=409, detail="Task has no pending validation command.")
    if any(item.get("status") == "awaiting_approval" for item in task.actions):
        raise HTTPException(status_code=409, detail="Resolve all proposed changes before validation.")
    try:
        command = validate_agent_command(request.command)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if command != task.validation_command:
        raise HTTPException(status_code=400, detail="Command does not match the proposed validation.")
    if not request.approved:
        task.status = "completed"
        task.stop_reason = "Validation was declined by the user."
        task.add_step("finish", "completed", task.stop_reason)
        return task.as_dict()

    if agent_tasks.transition(task.id, user_id, "awaiting_approval", "validating") is None:
        raise HTTPException(status_code=409, detail="Task validation is already in progress.")
    task.add_step("test", "running", "Running approved validation", input=command)
    try:
        result = terminal_service.execute_command(
            task.project_id,
            command,
            timeout_seconds=120,
        )
        safe_result = TerminalContext(
            command=command,
            exit_code=result.exit_code,
            stdout=result.stdout[:12000],
            stderr=result.stderr[:12000],
            success=result.success,
        )
        task.validation_result = safe_result.model_dump()
        if getattr(task, "status", None) == "cancelled":
            return task.as_dict()
        if result.success:
            task.status = "completed"
            task.add_step("test", "completed", "Validation passed", output=safe_result.stdout)
            task.add_step("finish", "completed", "Agent task completed")
        else:
            task.status = "failed"
            task.stop_reason = (
                "Maximum agent iterations reached."
                if task.iteration >= MAX_AGENT_ITERATIONS
                else "Validation failed; an agent correction requires explicit continuation."
            )
            task.add_step(
                "test", "failed", "Validation failed",
                output=safe_result.stderr or safe_result.stdout,
                error=f"Exit code {result.exit_code}",
            )
    except Exception as error:
        if getattr(task, "status", None) == "cancelled":
            return task.as_dict()
        task.status = "failed"
        task.iteration += 1
        task.stop_reason = (
            "Maximum agent iterations reached."
            if task.iteration >= MAX_AGENT_ITERATIONS
            else str(error)[:1000]
        )
        task.add_step("test", "failed", "Validation could not run", error=str(error))
    return task.as_dict()


@router.post("/tasks/{task_id}/cancel", response_model=AgentTaskResponse)
def cancel_agent_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = cast(int, current_user.id)
    _get_owned_task(task_id, user_id, db)
    task = agent_tasks.cancel(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Agent task not found.")
    return task.as_dict()


@router.post("/tasks/{task_id}/continue", status_code=202, response_model=AgentTaskResponse)
def continue_agent_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = cast(int, current_user.id)
    task = _get_owned_task(task_id, user_id, db)
    if task.status != "failed" or task.iteration >= MAX_AGENT_ITERATIONS:
        raise HTTPException(status_code=409, detail="Task cannot continue.")
    if task.validation_result is None:
        raise HTTPException(status_code=409, detail="Only failed validation can be continued.")
    if agent_tasks.transition(task.id, user_id, "failed", "planning") is None:
        raise HTTPException(status_code=409, detail="Task continuation is already in progress.")
    task.iteration += 1
    task.stop_reason = None
    background_tasks.add_task(_continue_task, task.id, user_id)
    return task.as_dict()


async def _continue_task(task_id: str, user_id: int) -> None:
    task = agent_tasks.get(task_id, user_id)
    if task is None or getattr(task, "status", None) == "cancelled":
        return
    db = SessionLocal()
    try:
        tools = AgentTools(db, task.project_id, user_id)
        prior_result = task.validation_result or {}
        context = tools.retrieve_context(task.task)[:20000]
        if task.current_context:
            context = f"{task.current_context}\n\n{context}"[:20000]
        prompt = (
            "Validation failed. Diagnose the failure and return a correction "
            "proposal only. Return a JSON object with plan, actions, and "
            "validation_command using the same schema as the original plan. "
            "Do not apply changes.\n\n"
            f"TASK:\n{task.task}\n\n"
            f"VALIDATION RESULT:\n{str(prior_result)[:12000]}"
        )
        response = await ai_service.chat(message=prompt, context=context, history=[])
        if getattr(task, "status", None) == "cancelled":
            return
        plan, actions, command = TaskPlanner.parse(response)
        for action in actions:
            tools.resolve_file(action.file_path)
        task.plan = plan
        task.actions = []
        for action in actions:
            project_file = tools.resolve_file(action.file_path)
            task.actions.append({
                "action": action.model_dump(),
                "status": "awaiting_approval",
                "error": None,
            })
        task.validation_command = command
        task.status = "awaiting_approval" if actions or command else "failed"
        task.stop_reason = None if actions or command else "No correction was proposed."
        if task.status == "failed":
            task.iteration = MAX_AGENT_ITERATIONS
        task.add_step("analyze", "completed", "Prepared a bounded correction proposal")
    except Exception as error:
        if getattr(task, "status", None) == "cancelled":
            return
        task.status = "failed"
        task.stop_reason = str(error)[:1000]
        task.add_step("finish", "failed", "Correction planning failed", error=str(error))
    finally:
        db.close()