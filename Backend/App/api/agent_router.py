import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import cast

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from App.api.ai_router import ai_service
from App.auth.auth import get_current_user
from App.database.database import SessionLocal, get_db
from App.models.project import Project
from App.models.project_file import ProjectFile
from App.models.user import User
from App.schema.agent_schema import (
    AgentApproval,
    AgentCommandApproval,
    AgentTaskCreate,
    AgentTaskResponse,
)
from App.schema.ai_schema import CodeAction, TerminalContext
from App.service.AI.agent.agent_task_service import (
    AI_AGENT_MAX_TOOL_CALLS,
    ACTIVE_AGENT_TASK_STATUSES,
    MAX_AGENT_ITERATIONS,
    MAX_AGENT_OUTPUT_CHARS,
    TERMINAL_AGENT_TASK_STATUSES,
    WAITING_AGENT_TASK_STATUSES,
    AgentToolCall,
    AgentTask,
    AgentTaskStore,
    TaskPlanner,
    validate_agent_command,
)
from App.service.AI.agent.agent_tools import (
    AgentToolError,
    AgentTools,
    CreateFileArguments,
    DeleteFileArguments,
    ExecuteCommandArguments,
    GetExecutionResultArguments,
    ListFilesArguments,
    ReadFileArguments,
    UpdateFileArguments,
)
from App.service.AI.code_action_service import (
    CodeActionService,
    MAX_RESULTING_FILE_CHARS,
    PatchAction,
    PatchNotFoundError,
    PatchValidationError,
    StalePatchError,
    UnsupportedPatchOperation,
)
from App.service.AI.index.repository_index_service import RepositoryIndexService
from App.service.project_workspace_service import (
    ProjectWorkspaceService,
    ProjectWorkspaceSyncError,
)
from App.service.sandbox_service import SandboxService


router = APIRouter(prefix="/ai/agent", tags=["AI Agent"])
logger = logging.getLogger(__name__)
agent_tasks = AgentTaskStore(SessionLocal)
sandbox_service = SandboxService()


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


def _run_validation_command(project_id: int, command: str, timeout_seconds: int = 120) -> TerminalContext:
    sandbox_enabled = os.getenv("ENABLE_SANDBOX", "true").lower() not in {"0", "false", "no"}
    if not sandbox_enabled or not sandbox_service.is_available():
        return TerminalContext(
            command=command,
            exit_code=127,
            stderr=(
                "Docker sandbox is unavailable; the approved command "
                "was not executed on the host."
            ),
            success=False,
        )

    result = sandbox_service.execute(project_id, command, timeout=timeout_seconds)
    return TerminalContext(
        command=command,
        exit_code=result.exit_code,
        stdout=result.stdout[:12000],
        stderr=result.stderr[:12000],
        success=result.success,
        timeout=result.timeout,
        created_files=result.created_files,
        modified_files=result.modified_files,
        deleted_files=result.deleted_files,
    )


def _run_project_validation_command(
    db: Session,
    project_id: int,
    user_id: int,
    command: str,
    timeout_seconds: int = 120,
) -> tuple[TerminalContext, list[dict[str, object]]]:
    sandbox_enabled = os.getenv("ENABLE_SANDBOX", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    if not sandbox_enabled or not sandbox_service.is_available():
        return (
            _run_validation_command(project_id, command, timeout_seconds),
            [],
        )

    result, changes = ProjectWorkspaceService(
        db,
        sandbox_service=sandbox_service,
    ).execute_command(
        project_id=project_id,
        user_id=user_id,
        command=command,
        timeout=timeout_seconds,
    )
    return (
        TerminalContext(
            command=command,
            exit_code=result.exit_code,
            stdout=result.stdout[:12000],
            stderr=result.stderr[:12000],
            success=result.success,
            timeout=result.timeout,
            created_files=result.created_files,
            modified_files=result.modified_files,
            deleted_files=result.deleted_files,
        ),
        changes,
    )


def _dispatch_agent_tool(
    task: AgentTask,
    tools: AgentTools,
    name: str,
    raw_arguments: dict[str, object],
) -> tuple[dict[str, object], CodeAction | None, str | None]:
    argument_models = {
        "list_files": ListFilesArguments,
        "read_file": ReadFileArguments,
        "create_file": CreateFileArguments,
        "update_file": UpdateFileArguments,
        "delete_file": DeleteFileArguments,
        "execute_command": ExecuteCommandArguments,
        "get_execution_result": GetExecutionResultArguments,
    }
    argument_model = argument_models.get(name)
    if argument_model is None:
        return {"success": False, "error": f"Unknown tool: {name}."}, None, None

    try:
        arguments = argument_model.model_validate(raw_arguments)
    except ValueError as error:
        return {
            "success": False,
            "error": f"Invalid arguments for {name}: {str(error)[:500]}",
        }, None, None

    try:
        if isinstance(arguments, ListFilesArguments):
            return {
                "success": True,
                "entries": tools.list_project_entries(arguments.path),
            }, None, None
        if isinstance(arguments, ReadFileArguments):
            return {"success": True, **tools.inspect_file(arguments.path)}, None, None
        if isinstance(arguments, CreateFileArguments):
            path = AgentTools._validate_path(arguments.path)
            if path in tools.list_project_files():
                return {
                    "success": False,
                    "error": "A file already exists at this path.",
                }, None, None
            action = CodeAction(
                type="code_change",
                operation="create_file",
                file_path=path,
                description=f"Create {path}",
                new_code=arguments.content,
            )
            return {
                "success": True,
                "status": "awaiting_user_approval",
                "path": path,
            }, action, None
        if isinstance(arguments, UpdateFileArguments):
            file_data = tools.inspect_file(arguments.path)
            current = str(file_data["content"])
            if len(current) > 20000:
                return {
                    "success": False,
                    "error": "The file exceeds the agent update limit.",
                }, None, None
            line_count = max(1, len(current.splitlines()))
            action = CodeAction(
                type="code_change",
                operation="replace",
                file_path=arguments.path,
                description=f"Update {arguments.path}",
                start_line=1,
                end_line=line_count,
                old_code=current,
                new_code=arguments.content,
            )
            return {
                "success": True,
                "status": "awaiting_user_approval",
                "path": arguments.path,
            }, action, None
        if isinstance(arguments, DeleteFileArguments):
            file_data = tools.inspect_file(arguments.path)
            current = str(file_data["content"])
            action = CodeAction(
                type="code_change",
                operation="delete",
                file_path=arguments.path,
                description=f"Delete {arguments.path}",
                start_line=1,
                end_line=max(1, len(current.splitlines())),
                old_code=current,
                new_code="",
            )
            return {
                "success": True,
                "status": "awaiting_user_approval",
                "path": arguments.path,
                "requires_confirmation": True,
            }, action, None
        if isinstance(arguments, ExecuteCommandArguments):
            command = validate_agent_command(arguments.command)
            return {
                "success": True,
                "status": "awaiting_user_approval",
                "command": command,
            }, None, command
        if isinstance(arguments, GetExecutionResultArguments):
            if task.validation_result is None:
                return {
                    "success": False,
                    "error": "No sandbox execution result is available yet.",
                }, None, None
            return {
                "success": True,
                "result": task.validation_result,
            }, None, None
    except (AgentToolError, ValueError) as error:
        return {"success": False, "error": str(error)[:1000]}, None, None

    return {"success": False, "error": "Tool arguments were not handled."}, None, None


async def _request_plan_with_tools(
    task: AgentTask,
    tools: AgentTools,
    context: str,
    plan_step,
) -> str | None:
    message = TaskPlanner.tool_prompt(task.task, task.tool_history)
    response = await ai_service.chat_json(
        message=message,
        context=context or "No relevant repository context was found.",
        history=[],
    )
    plan_step.output = response
    task.persist()

    while True:
        try:
            tool_call = TaskPlanner.parse_tool_call(response)
        except ValueError as error:
            task.tool_protocol_active = True
            task.tool_calls_used += 1
            result = {
                "success": False,
                "error": str(error),
            }
            task.tool_history.append({
                "name": "invalid_tool_call",
                "arguments": {},
                "result": result,
            })
            task.add_step(
                "tool",
                "failed",
                "Rejected malformed tool call",
                output=json.dumps(result),
            )
            if task.tool_calls_used >= AI_AGENT_MAX_TOOL_CALLS:
                raise ValueError(
                    "The agent reached its tool-call limit while recovering from malformed calls."
                )
            response = await ai_service.chat_json(
                message=TaskPlanner.tool_prompt(task.task, task.tool_history),
                context=context or "No relevant repository context was found.",
                history=[],
            )
            plan_step.output = response
            task.persist()
            continue

        if tool_call is None:
            return response
        if task.tool_calls_used >= AI_AGENT_MAX_TOOL_CALLS:
            raise ValueError(
                f"The agent reached the configured limit of {AI_AGENT_MAX_TOOL_CALLS} tool calls."
            )

        task.tool_protocol_active = True
        task.tool_calls_used += 1
        result, proposed_action, proposed_command = _dispatch_agent_tool(
            task,
            tools,
            tool_call.name,
            tool_call.arguments,
        )
        history_entry = {
            "name": tool_call.name,
            "arguments": tool_call.arguments,
            "result": result,
        }
        task.tool_history.append(history_entry)
        task.add_step(
            "tool",
            "completed" if result.get("success") else "failed",
            f"{tool_call.name} {'completed' if result.get('success') else 'failed'}",
            input=json.dumps(tool_call.arguments, ensure_ascii=True)[:2000],
            output=json.dumps(result, ensure_ascii=True)[:MAX_AGENT_OUTPUT_CHARS],
            error=None if result.get("success") else str(result.get("error", "")),
        )

        if proposed_action is not None:
            if len(task.actions) >= 10:
                raise ValueError("The agent exceeded the maximum number of proposed changes.")
            task.actions.append({
                "action": proposed_action.model_dump(),
                "status": "awaiting_approval",
                "error": None,
            })
            task.plan = [
                f"Review the proposed {proposed_action.operation} for {proposed_action.file_path}."
            ]
            task.status = "awaiting_approval"
            task.persist()
            return None
        if proposed_command is not None:
            task.validation_command = proposed_command
            task.plan = ["Review and approve the proposed sandbox command."]
            task.status = "awaiting_approval"
            task.persist()
            return None

        response = await ai_service.chat_json(
            message=TaskPlanner.tool_prompt(task.task, task.tool_history),
            context=context or "No relevant repository context was found.",
            history=[],
        )
        plan_step.output = response
        task.persist()


async def _plan_task(task_id: str, user_id: int) -> None:
    task = agent_tasks.get(task_id, user_id)
    if (
        task is None
        or task.status in TERMINAL_AGENT_TASK_STATUSES
        or task.status in WAITING_AGENT_TASK_STATUSES
    ):
        return
    task = agent_tasks.claim_worker(task_id, user_id)
    if task is None:
        return
    try:
        if task.status == "pending":
            if agent_tasks.transition(
                task.id,
                user_id,
                "pending",
                "planning",
            ) is None:
                return
        await _run_planning_task(task_id, user_id)
    except asyncio.CancelledError:
        if task.status in ACTIVE_AGENT_TASK_STATUSES:
            task.status = "paused"
            task.stop_reason = "Agent worker was interrupted; review completed steps before resuming."
            task.add_step(
                "recovery",
                "failed",
                "Agent worker was interrupted",
                error=task.stop_reason,
            )
        raise
    except Exception as error:
        logger.exception("Agent planning worker exited unexpectedly for task %s", task_id)
        if task.status not in TERMINAL_AGENT_TASK_STATUSES:
            task.status = "failed"
            task.stop_reason = str(error)[:1000]
            task.add_step(
                "finish",
                "failed",
                "Agent planning worker failed",
                error=str(error),
            )
    finally:
        if task.status in ACTIVE_AGENT_TASK_STATUSES:
            task.status = "failed"
            task.stop_reason = (
                "Agent planning worker exited without completing or waiting for approval."
            )
            task.add_step(
                "finish",
                "failed",
                "Agent planning worker exited unexpectedly",
                error=task.stop_reason,
            )
        try:
            task.persist()
        finally:
            agent_tasks.release_worker(task_id)


async def _run_planning_task(task_id: str, user_id: int) -> None:
    task = agent_tasks.get(task_id, user_id)
    if task is None or getattr(task, "status", None) == "cancelled":
        return
    db: Session | None = None
    try:
        task.add_step("retrieve", "running", "Retrieving project context")
        db = SessionLocal()
        tools = AgentTools(db, task.project_id, user_id)
        file_paths = tools.list_project_files()[:100]
        matches = tools.search_project(task.task)[:8]
        task.add_step("search", "completed", "Searched project files", output="\n".join(
            str(item.get("path", "")) for item in matches
        ))
        matched_paths = [str(result["path"]) for result in matches[:4]]
        relationships = tools.find_file_relationships(matched_paths)
        related_paths = [
            target if source in matched_paths else source
            for source, target in relationships
        ]
        inspect_paths = list(dict.fromkeys([*matched_paths, *related_paths]))[:6]
        inspected_paths = set(inspect_paths)
        relationships = [
            (source, target)
            for source, target in relationships
            if source in inspected_paths and target in inspected_paths
        ]
        inspected: list[str] = []
        for path in inspect_paths:
            try:
                file_data = tools.inspect_file(path)
            except AgentToolError:
                continue
            inspected.append(f"FILE: {path}\n{str(file_data['content'])[:4500]}")
            task.add_step("inspect", "completed", f"Inspected {path}")
        context = tools.retrieve_context(task.task)[:30000]
        if file_paths:
            tree = "PROJECT FILES:\n" + "\n".join(file_paths)
            context = f"{tree}\n\n{context}"
        else:
            context = (
                "PROJECT STATE: This project is empty. New files may be created to "
                "implement the task. Use create_file actions for the required new files.\n\n"
                f"{context}"
            )
        if relationships:
            relationship_lines = [
                f"{source} -> {target}" for source, target in relationships
            ]
            context = (
                "FILE RELATIONSHIPS (detected imports/references):\n"
                + "\n".join(relationship_lines)
                + "\n\n"
                + context
            )
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
        plan_step = task.add_step(
            "plan",
            "running",
            "Generating implementation plan",
            input="Preparing a plan and proposed changes from project context",
        )
        response = await _request_plan_with_tools(
            task,
            tools,
            context,
            plan_step,
        )
        if response is None:
            plan_step.status = "completed"
            plan_step.output = "Waiting for the requested user approval."
            task.persist()
            return
        plan_step.status = "completed"
        plan_step.output = response
        task.persist()
        if getattr(task, "status", None) == "cancelled":
            return
        try:
            plan, actions, command = TaskPlanner.parse(response)
        except ValueError as error:
            last_error = error
            for attempt in range(2):
                repair_step = task.add_step(
                    "plan",
                    "running",
                    "Repairing invalid implementation plan",
                    input=f"Repair attempt {attempt + 1} of 2",
                    error=str(last_error),
                )
                if getattr(task, "status", None) == "cancelled":
                    return
                response = await ai_service.chat_json(
                    message=TaskPlanner.repair_prompt(
                        task.task, response, str(last_error)
                    ),
                    context=context or "No relevant repository context was found.",
                    history=[],
                )
                repair_step.status = "completed"
                repair_step.output = response
                task.persist()
                if getattr(task, "status", None) == "cancelled":
                    return
                try:
                    plan, actions, command = TaskPlanner.parse(response)
                    break
                except ValueError as repair_error:
                    last_error = repair_error
            else:
                raise last_error
        if not actions and not command and not task.tool_protocol_active:
            previous_response = response
            for attempt in range(2):
                retry_step = task.add_step(
                    "plan",
                    "running",
                    "Retrying implementation plan with relevant files",
                    input=f"Implementation retry {attempt + 1} of 2",
                )
                if getattr(task, "status", None) == "cancelled":
                    return
                response = await ai_service.chat_json(
                    message=TaskPlanner.missing_actions_prompt(
                        task.task, previous_response
                    ),
                    context=context or "No relevant repository context was found.",
                    history=[],
                )
                retry_step.status = "completed"
                retry_step.output = response
                task.persist()
                if getattr(task, "status", None) == "cancelled":
                    return
                plan, actions, command = TaskPlanner.parse(response)
                if actions or command:
                    break
                previous_response = response

        if not actions and not command:
            task.plan = plan
            task.validation_command = None
            if task.tool_protocol_active:
                task.status = "completed"
                task.stop_reason = "The agent completed without proposing additional changes."
                task.add_step("finish", "completed", task.stop_reason)
                task.persist()
                return
            task.actions = []
            task.status = "failed"
            task.stop_reason = (
                "The agent could not prepare a code change from the available "
                "project files. Retry planning or specify the file and behavior "
                "you want changed."
            )
            task.add_step(
                "finish",
                "failed",
                "No code changes were proposed",
                error=task.stop_reason,
            )
            return
        for action in actions:
            if action.operation == "create_file":
                if action.file_path in tools.list_project_files():
                    raise ValueError("Agent create_file action path already exists in the project.")
                continue
            target = tools.resolve_file(action.file_path)
            if tools.get_file_path(target) != action.file_path:
                raise ValueError("Agent action path does not match the project file.")
        task.plan = plan
        previous_actions = task.actions if task.tool_protocol_active else []
        task.actions = list(previous_actions)
        if len(task.actions) + len(actions) > 10:
            raise ValueError("The agent exceeded the maximum number of proposed changes.")
        for action in actions:
            if action.operation == "create_file":
                task.actions.append({
                    "action": action.model_dump(),
                    "status": "awaiting_approval",
                    "error": None,
                })
                continue
            tools.resolve_file(action.file_path)
            task.actions.append({
                "action": action.model_dump(),
                "status": "awaiting_approval",
                "error": None,
            })
        task.validation_command = command
        task.add_step("plan", "completed", "Implementation plan prepared")
        if actions:
            task.add_step("code_action", "completed", f"Prepared {len(actions)} change(s) for review")
            task.status = "awaiting_approval"
        elif command:
            task.status = "awaiting_approval"
        else:
            task.status = "completed"
            task.stop_reason = "Planning completed without proposed file changes."
        task.persist()
    except Exception as error:
        logger.exception("Agent planning failed for task %s", task_id)
        task.status = "failed"
        task.stop_reason = str(error)[:1000]
        task.add_step("finish", "failed", "Agent planning failed", error=str(error))
    finally:
        if db is not None:
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
        and not AgentTools.contains_sensitive_content(request.context)
    ):
        current_context_parts.append(request.context[:20000])
    if request.selected_code:
        selected = request.selected_code
        if (
            AgentTools.is_safe_path(selected.file_path)
            and not AgentTools.contains_sensitive_content(selected.code)
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


@router.get("/tasks", response_model=list[AgentTaskResponse])
def list_agent_tasks(
    project_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if project_id is not None:
        project = (
            db.query(Project)
            .filter(
                Project.id == project_id,
                Project.user_id == current_user.id,
            )
            .first()
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
    return [
        task.as_dict()
        for task in agent_tasks.list_for_user(
            cast(int, current_user.id),
            project_id,
        )
    ]


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
    background_tasks: BackgroundTasks,
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
                if task.tool_protocol_active:
                    rejected_action = CodeAction.model_validate(
                        task.actions[index]["action"]
                    )
                    task.tool_history.append({
                        "name": f"{rejected_action.operation}_approval",
                        "arguments": {"path": rejected_action.file_path},
                        "result": {
                            "success": False,
                            "status": "rejected_by_user",
                        },
                    })
            task.status = "awaiting_approval"
            task.add_step("code_action", "completed", "Changes rejected by user")
        if not task.validation_command and not any(
            item.get("status") == "awaiting_approval" for item in task.actions
        ):
            failed_action = next(
                (
                    item for item in task.actions
                    if item.get("status") == "failed"
                ),
                None,
            )
            if failed_action is not None:
                task.status = "failed"
                task.stop_reason = str(
                    failed_action.get("error")
                    or "An approved change could not be applied."
                )
            elif task.tool_protocol_active:
                task.status = "planning"
                task.stop_reason = None
                task.add_step(
                    "tool",
                    "completed",
                    "Resuming the agent after the rejected file operation",
                )
                background_tasks.add_task(_plan_task, task.id, user_id)
            else:
                task.status = "completed"
                task.stop_reason = "All proposed changes were rejected."
                task.add_step("finish", "completed", task.stop_reason)
        task.persist()
        return task.as_dict()

    if agent_tasks.transition(task.id, user_id, "awaiting_approval", "executing") is None:
        raise HTTPException(status_code=409, detail="Task approval is already being processed.")
    for index in rejected:
        task.actions[index]["status"] = "rejected"
        if task.tool_protocol_active:
            rejected_action = CodeAction.model_validate(
                task.actions[index]["action"]
            )
            task.tool_history.append({
                "name": f"{rejected_action.operation}_approval",
                "arguments": {"path": rejected_action.file_path},
                "result": {
                    "success": False,
                    "status": "rejected_by_user",
                },
            })
    task.add_step("code_action", "running", "Applying approved changes")
    tools = AgentTools(db, task.project_id, user_id)
    change_results: list[dict[str, object]] = []
    for index in selected:
        if getattr(task, "status", None) == "cancelled":
            break
        item = task.actions[index]
        action = CodeAction.model_validate(item["action"])
        try:
            if action.operation == "delete":
                project_file = tools.resolve_file(action.file_path)
                file_id = cast(int, project_file.id)
                old_content = project_file.content or ""
                CodeActionService(db).delete_file(
                    project_id=task.project_id,
                    file_id=file_id,
                    user_id=user_id,
                    action=cast(PatchAction, action),
                )
                ProjectWorkspaceService(
                    db,
                    sandbox_service=sandbox_service,
                ).sync_file(
                    project_id=task.project_id,
                    user_id=user_id,
                    file_path=action.file_path,
                    content=None,
                )
                item["status"] = "applied"
                deleted_change = {
                    "path": action.file_path,
                    "content": "",
                    "file_id": file_id,
                    "deleted": True,
                }
                change_results.append(deleted_change)
                task.changes.append(deleted_change)
                if task.tool_protocol_active:
                    task.tool_history.append({
                        "name": "delete_file_approval",
                        "arguments": {"path": action.file_path},
                        "result": {
                            "success": True,
                            "status": "applied",
                            "path": action.file_path,
                        },
                    })
                task.persist()
                continue
            if action.operation == "create_file":
                updated_file = CodeActionService(db).apply_action(
                    project_id=task.project_id,
                    file_id=None,
                    user_id=user_id,
                    action=cast(PatchAction, action),
                )
                old_content = ""
            else:
                project_file = tools.resolve_file(action.file_path)
                old_content = project_file.content or ""
                updated_file = CodeActionService(db).apply_action(
                    project_id=task.project_id,
                    file_id=cast(int, project_file.id),
                    user_id=user_id,
                    action=cast(PatchAction, action),
                )
            ProjectWorkspaceService(
                db,
                sandbox_service=sandbox_service,
            ).sync_file(
                project_id=task.project_id,
                user_id=user_id,
                file_path=action.file_path,
                content=updated_file.content or "",
            )
            item["status"] = "applied"
            task.change_history.append({
                "path": action.file_path,
                "file_id": updated_file.id,
                "operation": action.operation,
                "old_hash": hashlib.sha256(old_content.encode("utf-8")).hexdigest(),
                "new_hash": hashlib.sha256((updated_file.content or "").encode("utf-8")).hexdigest(),
                "previous_content": old_content,
                "old_code": action.old_code,
                "new_code": action.new_code,
                "approved_by": user_id,
                "approved_at": datetime.now(timezone.utc).isoformat(),
            })
            if task.tool_protocol_active:
                task.tool_history.append({
                    "name": f"{action.operation}_approval",
                    "arguments": {"path": action.file_path},
                    "result": {
                        "success": True,
                        "status": "applied",
                        "path": action.file_path,
                    },
                })
            change_results.append({
                "path": action.file_path,
                "content": updated_file.content or "",
                "file_id": updated_file.id,
            })
            task.changes.append(change_results[-1])
            task.persist()
        except (
            AgentToolError, PatchNotFoundError, PatchValidationError,
            ProjectWorkspaceSyncError, StalePatchError,
            UnsupportedPatchOperation,
        ) as error:
            item["status"] = "failed"
            item["error"] = str(error)
            task.add_step("code_action", "failed", "Approved change was rejected", error=str(error))
            break

    if task.status not in {"failed", "cancelled"}:
        pending = any(item.get("status") == "awaiting_approval" for item in task.actions)
        failed = any(item.get("status") == "failed" for item in task.actions)
        if pending or task.validation_command:
            task.status = "awaiting_approval"
            task.stop_reason = None
        elif failed:
            task.status = "failed"
            task.stop_reason = next(
                (
                    str(item.get("error"))
                    for item in task.actions
                    if item.get("status") == "failed" and item.get("error")
                ),
                "An approved change could not be applied.",
            )
        elif task.tool_protocol_active:
            task.status = "planning"
            task.stop_reason = None
            task.add_step(
                "tool",
                "completed",
                "Resuming the agent after the approved file operation",
            )
            background_tasks.add_task(_plan_task, task.id, user_id)
        else:
            task.status = "completed"
            task.add_step("finish", "completed", "Approved changes applied")
    task.persist()
    response: dict[str, object] = task.as_dict()
    response["changes"] = change_results
    return response


@router.post("/tasks/{task_id}/undo", response_model=AgentTaskResponse)
def undo_agent_task_changes(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = cast(int, current_user.id)
    task = _get_owned_task(task_id, user_id, db)
    if task.status in {"pending", "planning", "executing", "validating"}:
        raise HTTPException(status_code=409, detail="Wait for the agent task to pause before undoing.")

    changes = [
        change for change in task.change_history
        if not change.get("rolled_back", False)
    ]
    if not changes:
        raise HTTPException(status_code=409, detail="No reversible AI changes remain.")

    current_contents: dict[int, str] = {}
    files: dict[int, ProjectFile] = {}
    for change in reversed(changes):
        file_id = change.get("file_id")
        previous_content = change.get("previous_content")
        expected_hash = change.get("new_hash")
        if (
            not isinstance(file_id, int)
            or not isinstance(previous_content, str)
            or not isinstance(expected_hash, str)
        ):
            raise HTTPException(
                status_code=409,
                detail="File has changed since this AI action. Review manually.",
            )
        project_file = files.get(file_id)
        if project_file is None:
            project_file = (
                db.query(ProjectFile)
                .filter(
                    ProjectFile.id == file_id,
                    ProjectFile.project_id == task.project_id,
                    ProjectFile.type == "file",
                )
                .with_for_update()
                .first()
            )
            if project_file is None:
                raise HTTPException(
                    status_code=409,
                    detail="File has changed since this AI action. Review manually.",
                )
            files[file_id] = project_file

        current_content = current_contents.get(
            file_id,
            project_file.content or "",
        )
        current_hash = hashlib.sha256(
            current_content.encode("utf-8")
        ).hexdigest()
        if current_hash != expected_hash:
            raise HTTPException(
                status_code=409,
                detail="File has changed since this AI action. Review manually.",
            )
        if len(previous_content) > MAX_RESULTING_FILE_CHARS:
            raise HTTPException(
                status_code=409,
                detail="This change is too large to roll back automatically. Review manually.",
            )
        current_contents[file_id] = previous_content

    for file_id, content in current_contents.items():
        project_file = files[file_id]
        project_file.content = content
        project_file.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    restored_changes: list[dict[str, object]] = []
    index_failures = 0
    for file_id, content in current_contents.items():
        project_file = files[file_id]
        restored_changes.append({
            "path": str(
                next(
                    (
                        change.get("path")
                        for change in reversed(changes)
                        if change.get("file_id") == file_id
                    ),
                    project_file.name,
                )
            ),
            "content": content,
            "file_id": file_id,
        })
        try:
            RepositoryIndexService(db, task.project_id).create_or_update_index(
                project_file
            )
        except Exception as error:
            index_failures += 1
            logger.warning(
                "Index refresh after agent rollback failed for project %s file %s (%s).",
                task.project_id,
                file_id,
                type(error).__name__,
            )

    reverted_paths = {change.get("path") for change in changes}
    for item in task.actions:
        action = item.get("action")
        if (
            isinstance(action, dict)
            and action.get("file_path") in reverted_paths
            and item.get("status") == "applied"
        ):
            item["status"] = "reverted"
    for change in changes:
        change["rolled_back"] = True
    task.changes = restored_changes
    task.stop_reason = (
        "AI changes were restored, but repository indexing needs to be retried."
        if index_failures
        else "AI changes were rolled back safely."
    )
    task.add_step(
        "rollback",
        "completed",
        f"Safely restored {len(restored_changes)} file(s) from this task.",
    )
    if index_failures:
        task.add_step(
            "index",
            "failed",
            "Files restored but repository index refresh failed.",
            error="Retry project indexing.",
        )
    task.persist()
    response = task.as_dict()
    response["changes"] = restored_changes
    return response


@router.post("/tasks/{task_id}/validate", response_model=AgentTaskResponse)
def run_agent_validation(
    task_id: str,
    request: AgentCommandApproval,
    background_tasks: BackgroundTasks,
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
        if task.tool_protocol_active:
            task.tool_history.append({
                "name": "execute_command_approval",
                "arguments": {"command": command},
                "result": {
                    "success": False,
                    "status": "rejected_by_user",
                },
            })
            task.validation_command = None
            task.status = "planning"
            task.stop_reason = None
            task.add_step(
                "tool",
                "completed",
                "Resuming the agent after the command was declined",
            )
            background_tasks.add_task(_plan_task, task.id, user_id)
            task.persist()
            return task.as_dict()
        task.status = "completed"
        task.stop_reason = "Validation was declined by the user."
        task.add_step("finish", "completed", task.stop_reason)
        return task.as_dict()

    if agent_tasks.transition(task.id, user_id, "awaiting_approval", "validating") is None:
        raise HTTPException(status_code=409, detail="Task validation is already in progress.")
    task.add_step("test", "running", "Running approved validation", input=command)
    try:
        safe_result, synchronized_changes = _run_project_validation_command(
            db=db,
            project_id=task.project_id,
            user_id=user_id,
            command=command,
            timeout_seconds=120,
        )
        task.validation_result = safe_result.model_dump()
        if synchronized_changes:
            task.changes = synchronized_changes
        if getattr(task, "status", None) == "cancelled":
            task.persist()
            return task.as_dict()
        if task.tool_protocol_active:
            task.tool_history.append({
                "name": "execute_command_result",
                "arguments": {"command": command},
                "result": safe_result.model_dump(),
            })
            task.validation_command = None
            task.status = "planning"
            task.stop_reason = None
            task.add_step(
                "tool",
                "completed" if safe_result.success else "failed",
                "Sandbox command finished; returning its result to the agent",
                output=(safe_result.stdout or safe_result.stderr)[:MAX_AGENT_OUTPUT_CHARS],
                error=None if safe_result.success else f"Exit code {safe_result.exit_code}",
            )
            background_tasks.add_task(_plan_task, task.id, user_id)
            task.persist()
            return task.as_dict()
        if safe_result.success:
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
                error=f"Exit code {safe_result.exit_code}",
            )
    except ProjectWorkspaceSyncError as error:
        task.status = "failed"
        task.stop_reason = str(error)[:1000]
        task.add_step(
            "sync",
            "failed",
            "Sandbox changes could not be synchronized to project files",
            error=str(error),
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
    task.persist()
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
    if task.status == "paused":
        if agent_tasks.transition(task.id, user_id, "paused", "planning") is None:
            raise HTTPException(status_code=409, detail="Task recovery is already in progress.")
        task.stop_reason = None
        task.add_step(
            "recovery",
            "completed",
            "User requested recovery; the project will be inspected again before changes are proposed.",
        )
        background_tasks.add_task(_plan_task, task.id, user_id)
        return task.as_dict()
    if task.status != "failed" or task.iteration >= MAX_AGENT_ITERATIONS:
        raise HTTPException(status_code=409, detail="Task cannot continue.")
    if task.validation_result is None:
        if agent_tasks.transition(task.id, user_id, "failed", "planning") is None:
            raise HTTPException(status_code=409, detail="Task retry is already in progress.")
        task.iteration += 1
        task.stop_reason = None
        task.add_step(
            "recovery",
            "completed",
            "User requested a retry after planning failed.",
        )
        background_tasks.add_task(_plan_task, task.id, user_id)
        return task.as_dict()
    if agent_tasks.transition(task.id, user_id, "failed", "planning") is None:
        raise HTTPException(status_code=409, detail="Task continuation is already in progress.")
    task.iteration += 1
    task.stop_reason = None
    task.persist()
    background_tasks.add_task(_continue_task, task.id, user_id)
    return task.as_dict()


async def _continue_task(task_id: str, user_id: int) -> None:
    task = agent_tasks.get(task_id, user_id)
    if (
        task is None
        or task.status in TERMINAL_AGENT_TASK_STATUSES
        or task.status in WAITING_AGENT_TASK_STATUSES
    ):
        return
    task = agent_tasks.claim_worker(task_id, user_id)
    if task is None:
        return
    try:
        await _run_continuation_task(task_id, user_id)
    except asyncio.CancelledError:
        if task.status in ACTIVE_AGENT_TASK_STATUSES:
            task.status = "paused"
            task.stop_reason = "Agent worker was interrupted; review completed steps before resuming."
            task.add_step(
                "recovery",
                "failed",
                "Agent worker was interrupted",
                error=task.stop_reason,
            )
        raise
    except Exception as error:
        logger.exception("Agent continuation worker exited unexpectedly for task %s", task_id)
        if task.status not in TERMINAL_AGENT_TASK_STATUSES:
            task.status = "failed"
            task.stop_reason = str(error)[:1000]
            task.add_step(
                "finish",
                "failed",
                "Agent continuation worker failed",
                error=str(error),
            )
    finally:
        if task.status in ACTIVE_AGENT_TASK_STATUSES:
            task.status = "failed"
            task.stop_reason = (
                "Agent continuation worker exited without completing or waiting for approval."
            )
            task.add_step(
                "finish",
                "failed",
                "Agent continuation worker exited unexpectedly",
                error=task.stop_reason,
            )
        try:
            task.persist()
        finally:
            agent_tasks.release_worker(task_id)


async def _run_continuation_task(task_id: str, user_id: int) -> None:
    task = agent_tasks.get(task_id, user_id)
    if task is None or getattr(task, "status", None) == "cancelled":
        return
    db: Session | None = None
    try:
        db = SessionLocal()
        tools = AgentTools(db, task.project_id, user_id)
        prior_result = task.validation_result or {}
        context = tools.retrieve_context(task.task)[:20000]
        if task.current_context:
            context = f"{task.current_context}\n\n{context}"[:20000]
        prompt = (
            "Validation failed. Diagnose the failure and return a correction "
            "proposal only. Return a JSON object with plan, actions, and "
            "validation_command using the same schema as the original plan. "
            "Each action must be a CodeAction object with type=code_change, "
            "operation=replace or create_file, file_path, description, and "
            "the operation's required fields. For replace, include exact "
            "start_line, end_line, old_code, and new_code from the provided "
            "project context. For a new file, use operation=create_file and "
            "put its full contents in new_code. Do not use type=create_file "
            "or a content field. Use only commands from the existing agent "
            "validation allowlist; use null if no validation command applies. "
            "Do not apply changes.\n\n"
            f"TASK:\n{task.task}\n\n"
            f"VALIDATION RESULT:\n{str(prior_result)[:12000]}"
        )
        plan_step = task.add_step(
            "plan",
            "running",
            "Generating a correction proposal",
            input="Analyzing the failed validation result",
        )
        response = await ai_service.chat_json(
            message=prompt,
            context=context,
            history=[],
        )
        plan_step.status = "completed"
        plan_step.output = response
        task.persist()
        if getattr(task, "status", None) == "cancelled":
            return
        plan, actions, command = TaskPlanner.parse(response)
        for action in actions:
            if action.operation != "create_file":
                tools.resolve_file(action.file_path)
        task.plan = plan
        task.actions = []
        for action in actions:
            if action.operation == "create_file":
                task.actions.append({
                    "action": action.model_dump(),
                    "status": "awaiting_approval",
                    "error": None,
                })
                continue
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
        task.persist()
    except Exception as error:
        if getattr(task, "status", None) == "cancelled":
            return
        task.status = "failed"
        task.stop_reason = str(error)[:1000]
        task.add_step("finish", "failed", "Correction planning failed", error=str(error))
        task.persist()
    finally:
        if db is not None:
            db.close()