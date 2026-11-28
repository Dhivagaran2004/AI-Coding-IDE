import asyncio
import hashlib
import json
import logging
import os
import subprocess
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
    AgentClarification,
    AgentCommandApproval,
    AgentPlan,
    AgentTaskCreate,
    AgentTaskResponse,
)
from App.schema.ai_schema import CodeAction, TerminalContext
from App.service.AI.agent.agent_task_service import (
    AI_AGENT_MAX_CORRECTION_RETRIES,
    AI_AGENT_MAX_TOOL_CALLS,
    ACTIVE_AGENT_TASK_STATUSES,
    MAX_AGENT_ITERATIONS,
    MAX_AGENT_ACTIONS,
    MAX_AGENT_OUTPUT_CHARS,
    TERMINAL_AGENT_TASK_STATUSES,
    WAITING_AGENT_TASK_STATUSES,
    AgentToolCall,
    AgentTask,
    AgentTaskStore,
    TaskPlanner,
    validate_agent_command,
)
from App.service.AI.agent.agent_sandbox_qa import (
    AgentSandboxQAError,
    build_sandbox_snapshot,
    is_missing_dependency,
    run_sandbox_validation,
    select_validation_command,
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
from App.service.sandbox_preview_service import SandboxPreviewService
from App.service.sandbox_service import SandboxService


router = APIRouter(prefix="/ai/agent", tags=["AI Agent"])
logger = logging.getLogger(__name__)
agent_tasks = AgentTaskStore(SessionLocal)
sandbox_service = SandboxService()
preview_service = SandboxPreviewService()


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
    if task.preview_url and not preview_service.is_active(task_id):
        task.preview_url = None
        task.persist()
    return task


def _plan_task_rows(task: AgentTask) -> list[dict[str, object]]:
    if task.plan_details is None:
        return []
    rows = task.plan_details.get("tasks", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _plan_task_actions(
    task: AgentTask,
    plan_task_id: str,
) -> list[dict[str, object]]:
    return [
        item for item in task.actions
        if item.get("plan_task_id") == plan_task_id
    ]


def _assign_plan_action_records(
    task: AgentTask,
    plan_details: AgentPlan,
    actions: list[CodeAction],
    prior_actions: list[dict[str, object]],
) -> None:
    file_owners: dict[str, str] = {}
    for plan_task in plan_details.tasks:
        for file_path in plan_task.expected_files:
            if file_path in file_owners:
                raise ValueError(
                    f"Expected file {file_path} is assigned to multiple plan tasks."
                )
            file_owners[file_path] = plan_task.id

    task.actions = list(prior_actions)
    for item in task.actions:
        action_data = item.get("action")
        if not isinstance(action_data, dict):
            continue
        action = CodeAction.model_validate(action_data)
        plan_task_id = file_owners.get(action.file_path)
        if plan_task_id is not None:
            item["plan_task_id"] = plan_task_id
        else:
            item.pop("plan_task_id", None)

    for action in actions:
        plan_task_id = file_owners.get(action.file_path)
        if plan_task_id is None:
            raise ValueError(
                f"Action path {action.file_path} is not assigned to a plan task."
            )
        task.actions.append({
            "action": action.model_dump(),
            "status": "scheduled",
            "plan_task_id": plan_task_id,
            "error": None,
        })


def _record_plan_task_status(
    task: AgentTask,
    plan_task_id: str,
    status: str,
    description: str,
    verification_notes: str | None = None,
) -> None:
    task.set_plan_task_status(
        plan_task_id,
        status,
        description,
        verification_notes=verification_notes,
    )


def _activate_ready_plan_tasks(task: AgentTask) -> None:
    made_progress = True
    while made_progress:
        made_progress = False
        task_rows = _plan_task_rows(task)
        tasks_by_id = {
            str(row.get("id")): row
            for row in task_rows
            if row.get("id")
        }
        for row in task_rows:
            plan_task_id = str(row.get("id", ""))
            if not plan_task_id or row.get("status") != "pending":
                continue
            dependencies = row.get("dependencies", [])
            if not isinstance(dependencies, list):
                raise ValueError(f"Plan task {plan_task_id} has invalid dependencies.")
            if any(
                tasks_by_id.get(str(dependency), {}).get("status") != "completed"
                for dependency in dependencies
            ):
                continue
            if (
                row.get("task_type") == "analysis"
                and not dependencies
                and not row.get("expected_files")
            ):
                row["verification_status"] = "not_required"
                _record_plan_task_status(
                    task,
                    plan_task_id,
                    "completed",
                    "Repository analysis completed during planning.",
                )
                made_progress = True
                continue
            _record_plan_task_status(
                task,
                plan_task_id,
                "in_progress",
                "Plan task is ready for execution.",
            )
            for action_item in _plan_task_actions(task, plan_task_id):
                if action_item.get("status") == "scheduled":
                    action_item["status"] = "awaiting_approval"
            made_progress = True


def _sync_plan_task_actions(task: AgentTask) -> None:
    for row in _plan_task_rows(task):
        plan_task_id = str(row.get("id", ""))
        if not plan_task_id or row.get("status") != "in_progress":
            continue
        action_items = _plan_task_actions(task, plan_task_id)
        if not action_items:
            continue
        statuses = [item.get("status") for item in action_items]
        if any(status in {"failed", "rejected", "blocked"} for status in statuses):
            _record_plan_task_status(
                task,
                plan_task_id,
                "blocked",
                "A required file action failed or was rejected.",
            )
        elif all(status == "applied" for status in statuses):
            _record_plan_task_status(
                task,
                plan_task_id,
                "applied",
                "All approved file actions for this task were applied; task verification is still required.",
            )

    made_progress = True
    while made_progress:
        made_progress = False
        tasks_by_id = {
            str(row.get("id")): row
            for row in _plan_task_rows(task)
            if row.get("id")
        }
        for row in _plan_task_rows(task):
            if row.get("status") != "pending":
                continue
            dependencies = row.get("dependencies", [])
            if not isinstance(dependencies, list):
                continue
            if any(
                tasks_by_id.get(str(dependency), {}).get("status") == "blocked"
                for dependency in dependencies
            ):
                plan_task_id = str(row.get("id", ""))
                if not plan_task_id:
                    continue
                _record_plan_task_status(
                    task,
                    plan_task_id,
                    "blocked",
                    "A prerequisite plan task is blocked.",
                )
                for action_item in _plan_task_actions(task, plan_task_id):
                    if action_item.get("status") == "scheduled":
                        action_item["status"] = "blocked"
                made_progress = True


def _plan_validation_blockers(task: AgentTask) -> list[str]:
    blockers = []
    if any(
        item.get("status") in {"scheduled", "awaiting_approval"}
        for item in task.actions
    ):
        blockers.append("pending file actions")
    for row in _plan_task_rows(task):
        status = row.get("status")
        task_type = row.get("task_type")
        if status == "pending":
            blockers.append("pending plan tasks")
        elif status == "in_progress" and task_type != "verification":
            blockers.append("unfinished plan tasks")
        elif status == "blocked":
            blockers.append("blocked plan tasks")
    return blockers


def _advance_plan_execution(task: AgentTask) -> None:
    if task.plan_details is None:
        return
    _sync_plan_task_actions(task)
    for row in _plan_task_rows(task):
        if row.get("status") != "applied":
            continue
        plan_task_id = str(row.get("id", ""))
        if not plan_task_id:
            continue
        action_items = _plan_task_actions(task, plan_task_id)
        if not action_items or any(
            item.get("status") != "applied" for item in action_items
        ):
            continue
        dependencies = row.get("dependencies", [])
        tasks_by_id = {
            str(item.get("id")): item
            for item in _plan_task_rows(task)
            if item.get("id")
        }
        if not isinstance(dependencies, list) or any(
            tasks_by_id.get(str(dependency), {}).get("status") != "completed"
            for dependency in dependencies
        ):
            continue
        _record_plan_task_status(
            task,
            plan_task_id,
            "completed",
            "Approved changes were applied; the agent will verify the plan automatically.",
        )
    _activate_ready_plan_tasks(task)
    _sync_plan_task_actions(task)
    validation_succeeded = bool(
        task.validation_result
        and task.validation_result.get("success") is True
    )
    validation_unavailable = (
        (task.validation_command is None and task.validation_result is None)
        or bool(
            task.validation_result
            and task.validation_result.get("executed") is False
        )
    )
    if validation_succeeded or validation_unavailable:
        for row in _plan_task_rows(task):
            plan_task_id = str(row.get("id", ""))
            if not plan_task_id:
                continue
            if (
                row.get("status") == "completed"
                and row.get("verification_status") == "pending"
                and _plan_task_actions(task, plan_task_id)
            ):
                row["verification_status"] = "not_verified"
                criteria = row.get("acceptance_criteria", [])
                if isinstance(criteria, list):
                    evidence = (
                        "Project validation passed, but this criterion was not "
                        "individually assessed."
                        if validation_succeeded
                        else "Automated validation was unavailable; this criterion "
                        "could not be individually assessed."
                    )
                    row["acceptance_results"] = [
                        {
                            "criterion": str(criterion),
                            "status": "not_verified",
                            "evidence": evidence,
                        }
                        for criterion in criteria
                    ]
            elif (
                row.get("status") == "in_progress"
                and row.get("task_type") == "verification"
            ):
                row["verification_status"] = (
                    "verified" if validation_succeeded else "not_verified"
                )
                criteria = row.get("acceptance_criteria", [])
                if isinstance(criteria, list):
                    evidence = (
                        "The agent's project validation command completed successfully."
                        if validation_succeeded
                        else "The agent could not run project validation automatically."
                    )
                    row["acceptance_results"] = [
                        {
                            "criterion": str(criterion),
                            "status": "met" if validation_succeeded else "not_verified",
                            "evidence": evidence,
                        }
                        for criterion in criteria
                    ]
                _record_plan_task_status(
                    task,
                    plan_task_id,
                    "completed",
                    (
                        "The agent's project validation completed successfully."
                        if validation_succeeded
                        else "Project validation could not run automatically."
                    ),
                )
    _activate_ready_plan_tasks(task)
    _sync_plan_task_actions(task)
    task_rows = _plan_task_rows(task)
    action_statuses = [item.get("status") for item in task.actions]
    all_tasks_completed = all(
        row.get("status") == "completed" for row in task_rows
    )
    if any(row.get("verification_status") == "failed" for row in task_rows):
        task.verification_status = "failed"
    elif all_tasks_completed and all(
        row.get("verification_status") in {"verified", "not_required"}
        for row in task_rows
    ):
        task.verification_status = "verified"
    elif any(
        row.get("verification_status") == "not_verified"
        or row.get("status") == "applied"
        for row in task_rows
    ):
        task.verification_status = "not_verified"
    else:
        task.verification_status = "pending"
    if "awaiting_approval" in action_statuses:
        task.status = "awaiting_approval"
        task.stop_reason = None
    elif any(row.get("status") == "blocked" for row in task_rows):
        task.status = "failed"
        task.stop_reason = "A plan task is blocked; dependent tasks cannot proceed."
    elif any(status == "scheduled" for status in action_statuses):
        task.status = "awaiting_validation"
        task.stop_reason = (
            "Verify and complete the applied prerequisite task(s) to unlock dependent work."
        )
    elif task.validation_command and not task.validation_result:
        blockers = _plan_validation_blockers(task)
        if not blockers:
            task.status = "awaiting_approval"
            task.stop_reason = None
        else:
            task.status = "awaiting_validation"
            task.stop_reason = (
                "Complete the current plan task(s) before running project validation."
            )
    elif (
        task.validation_result
        and task.validation_result.get("success") is False
        and task.validation_result.get("executed") is not False
        and not any(
            item.get("name") == "execute_command_result"
            and item.get("result") == task.validation_result
            for item in task.tool_history
        )
    ):
        task.status = "failed"
        task.verification_status = "failed"
        task.stop_reason = (
            "Automated project validation failed; continue to propose a correction."
        )
    elif all_tasks_completed and task.verification_status == "verified":
        task.status = "completed"
        task.stop_reason = "All plan tasks are complete."
    elif all_tasks_completed and task.verification_status == "not_verified":
        task.status = "completed"
        task.stop_reason = (
            "Plan tasks are complete. Project validation passed, but some acceptance "
            "criteria were not individually assessed."
            if validation_succeeded
            else "Plan tasks are complete, but automated verification was unavailable."
        )
    else:
        task.status = "awaiting_validation"
        task.stop_reason = (
            "Plan task changes are applied but acceptance criteria are not verified."
            if task.verification_status == "not_verified"
            else "Plan tasks require verification before completion."
        )
    task.persist()


def _complete_plan_tasks_after_validation(task: AgentTask) -> None:
    _advance_plan_execution(task)


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
            executed=False,
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


async def _run_sandbox_plan_qa(
    task: AgentTask,
    tools: AgentTools,
    context: str,
    response: str,
    plan_details: AgentPlan,
    actions: list[CodeAction],
    command: str | None,
) -> tuple[AgentPlan, list[CodeAction], str | None]:
    try:
        snapshot = build_sandbox_snapshot(tools, actions)
    except AgentSandboxQAError:
        raise
    proposed_command = command
    qa_command = select_validation_command(snapshot, proposed_command)
    if qa_command is None:
        task.verification_status = "not_verified"
        task.add_step(
            "sandbox_qa",
            "not_verified",
            "No safe project validation command could be selected",
            error=(
                "The agent must propose a project-local test or build command; "
                "no files were executed outside Docker."
            ),
        )
        return plan_details, actions, proposed_command

    qa_command = validate_agent_command(qa_command)
    sandbox_enabled = os.getenv("ENABLE_SANDBOX", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    if not sandbox_enabled or not sandbox_service.is_available():
        task.verification_status = "not_verified"
        task.add_step(
            "sandbox_qa",
            "not_verified",
            "Sandbox validation is unavailable",
            input=qa_command,
            error="Docker sandbox is unavailable; the validation command was not run.",
        )
        return plan_details, actions, proposed_command

    result: TerminalContext | None = None
    for attempt in range(1, MAX_AGENT_ITERATIONS + 1):
        task.add_step(
            "test",
            "running",
            f"Running sandbox validation attempt {attempt}",
            input=qa_command,
        )
        result = run_sandbox_validation(
            sandbox=sandbox_service,
            project_id=task.project_id,
            files=snapshot,
            command=qa_command,
            timeout_seconds=120,
        )
        task.add_step(
            "test",
            "completed" if result.success else "failed",
            "Sandbox validation passed" if result.success else "Sandbox validation failed",
            input=qa_command,
            output=(result.stdout or result.stderr)[:MAX_AGENT_OUTPUT_CHARS],
            error=None if result.success else f"Exit code {result.exit_code}",
        )
        if result.success or not result.executed:
            break
        if is_missing_dependency(result):
            task.add_step(
                "dependency",
                "not_verified",
                "Sandbox validation is blocked by an unavailable package",
                input=qa_command,
                error=(
                    "The sandbox has no internet access and the required package "
                    "is not preinstalled or cached. Add it to the project manifest "
                    "and provide it in the offline sandbox cache before retrying."
                ),
            )
            break
        if attempt >= MAX_AGENT_ITERATIONS:
            break

        diagnosis = await ai_service.chat_json(
            message=TaskPlanner.sandbox_diagnosis_prompt(
                task=task.task,
                command=qa_command,
                stdout=result.stdout,
                stderr=result.stderr,
                attempt=attempt,
            ),
            context=context or "No relevant repository context was found.",
            history=[],
        )
        task.add_step(
            "diagnosis",
            "completed",
            f"Diagnosed sandbox failure {attempt}",
            output=diagnosis,
        )
        failure_summary = (
            f"Sandbox validation command failed: {qa_command}\n"
            f"Exit code: {result.exit_code}\n"
            f"STDOUT:\n{result.stdout[:6000]}\n"
            f"STDERR:\n{result.stderr[:6000]}\n"
            f"ROOT-CAUSE ANALYSIS:\n{diagnosis[:6000]}"
        )
        repair_response = await ai_service.chat_json(
            message=TaskPlanner.repair_prompt(
                task=TaskPlanner.include_clarifications(
                    task.task,
                    task.clarification_history,
                ),
                response=response,
                error=failure_summary,
            ),
            context=context or "No relevant repository context was found.",
            history=[],
        )
        repaired = TaskPlanner.parse_planning_result(
            repair_response,
            existing_actions=actions,
        )
        if repaired.status != "ready" or repaired.plan is None:
            task.add_step(
                "repair",
                "failed",
                "The agent could not prepare a safe correction",
                output=repair_response,
            )
            break
        if not repaired.actions:
            task.add_step(
                "repair",
                "failed",
                "The agent returned no correction actions",
                output=repair_response,
            )
            break

        plan_details = repaired.plan
        actions = repaired.actions
        response = repair_response
        snapshot = build_sandbox_snapshot(tools, actions)
        if repaired.validation_command:
            proposed_command = validate_agent_command(
                repaired.validation_command
            )
        qa_command = validate_agent_command(
            select_validation_command(snapshot, proposed_command)
            or qa_command
        )
        task.add_step(
            "repair",
            "completed",
            f"Prepared sandbox correction {attempt}",
            output=repair_response,
        )

    if result is not None and result.executed and not result.success:
        task.validation_result = result.model_dump()
        task.add_step(
            "sandbox_qa",
            "failed",
            "Sandbox QA did not pass; review the reported issues before applying changes",
            input=qa_command,
            output=(result.stdout or result.stderr)[:MAX_AGENT_OUTPUT_CHARS],
        )
    return plan_details, actions, proposed_command


def _start_task_preview(task: AgentTask, db: Session, user_id: int) -> None:
    if task.preview_url and preview_service.is_active(task.id):
        return
    task.preview_url = None
    try:
        tools = AgentTools(db, task.project_id, user_id)
        files = build_sandbox_snapshot(tools, [])
        preview_url = preview_service.start(task.id, files)
    except (
        AgentSandboxQAError,
        AgentToolError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        logger.warning(
            "Sandbox preview could not start for agent task %s: %s",
            task.id,
            error,
        )
        task.add_step(
            "preview",
            "failed",
            "Sandbox preview could not start",
            error=str(error)[:1000],
        )
        return
    if preview_url is None:
        task.add_step(
            "preview",
            "not_verified",
            "No supported web entry point was found for preview",
        )
        return
    task.preview_url = preview_url
    task.add_step(
        "preview",
        "completed",
        "Started a static preview of the approved project files",
        output=preview_url,
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
    message = TaskPlanner.tool_prompt(
        task.task,
        task.tool_history,
        task.clarification_history,
    )
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
                message=TaskPlanner.tool_prompt(
                    task.task,
                    task.tool_history,
                    task.clarification_history,
                ),
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
            if len(task.actions) >= MAX_AGENT_ACTIONS:
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
            message=TaskPlanner.tool_prompt(
                task.task,
                task.tool_history,
                task.clarification_history,
            ),
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
        context = tools.retrieve_context(task.task)[:22000]
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
        context = (
            "REPOSITORY INTELLIGENCE (detected from safe project files):\n"
            f"{tools.project_intelligence()}\n\n{context}"
        )[:30000]
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
        existing_actions = [
            CodeAction.model_validate(item["action"])
            for item in task.actions
            if item.get("status") == "applied"
            and isinstance(item.get("action"), dict)
        ]
        try:
            planning_result = TaskPlanner.parse_planning_result(
                response,
                existing_actions=existing_actions,
            )
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
                        TaskPlanner.include_clarifications(
                            task.task,
                            task.clarification_history,
                        ),
                        response,
                        str(last_error),
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
                    planning_result = TaskPlanner.parse_planning_result(
                        response,
                        existing_actions=existing_actions,
                    )
                    break
                except ValueError as repair_error:
                    last_error = repair_error
            else:
                raise last_error
        if (
            planning_result.status == "clarification_required"
            and TaskPlanner.is_action_limit_clarification(
                planning_result.clarification_questions
            )
        ):
            phase_error = ValueError(
                "The action limit is internal. Return a ready plan for the first "
                "coherent phase with at most 40 actions; do not ask the user to "
                "choose a phase."
            )
            for attempt in range(2):
                repair_step = task.add_step(
                    "plan",
                    "running",
                    "Preparing the first bounded implementation phase",
                    input=f"Phase planning attempt {attempt + 1} of 2",
                    error=str(phase_error),
                )
                if getattr(task, "status", None) == "cancelled":
                    return
                response = await ai_service.chat_json(
                    message=TaskPlanner.repair_prompt(
                        TaskPlanner.include_clarifications(
                            task.task,
                            task.clarification_history,
                        ),
                        response,
                        str(phase_error),
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
                    planning_result = TaskPlanner.parse_planning_result(
                        response,
                        existing_actions=existing_actions,
                    )
                except ValueError as repair_error:
                    phase_error = repair_error
                    continue
                if (
                    planning_result.status == "clarification_required"
                    and TaskPlanner.is_action_limit_clarification(
                        planning_result.clarification_questions
                    )
                ):
                    phase_error = ValueError(
                        "The planner again asked about the internal action limit."
                    )
                    continue
                break
            else:
                raise phase_error
        if planning_result.status == "clarification_required":
            task.clarification_questions = planning_result.clarification_questions
            task.validation_command = None
            task.actions = [
                item for item in task.actions
                if item.get("status") == "applied"
            ]
            task.status = "awaiting_clarification"
            task.stop_reason = "The agent needs clarification before it can prepare a safe implementation plan."
            task.add_step(
                "clarification",
                "completed",
                "Waiting for user clarification",
                output="\n".join(task.clarification_questions),
            )
            task.persist()
            return

        assert planning_result.plan is not None
        plan_details = planning_result.plan
        plan = [item.description for item in plan_details.tasks]
        actions = planning_result.actions
        command = planning_result.validation_command
        if (
            not actions
            and not command
            and not task.tool_protocol_active
            and TaskPlanner.request_expects_changes(task.task)
        ):
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
                        TaskPlanner.include_clarifications(
                            task.task,
                            task.clarification_history,
                        ),
                        previous_response,
                    ),
                    context=context or "No relevant repository context was found.",
                    history=[],
                )
                retry_step.status = "completed"
                retry_step.output = response
                task.persist()
                if getattr(task, "status", None) == "cancelled":
                    return
                try:
                    planning_result = TaskPlanner.parse_planning_result(
                        response,
                        existing_actions=existing_actions,
                    )
                except ValueError:
                    raise
                if planning_result.status == "clarification_required":
                    task.clarification_questions = planning_result.clarification_questions
                    task.status = "awaiting_clarification"
                    task.stop_reason = "The agent needs clarification before it can prepare a safe implementation plan."
                    task.add_step(
                        "clarification",
                        "completed",
                        "Waiting for user clarification",
                        output="\n".join(task.clarification_questions),
                    )
                    task.persist()
                    return
                assert planning_result.plan is not None
                plan_details = planning_result.plan
                plan = [item.description for item in plan_details.tasks]
                actions = planning_result.actions
                command = planning_result.validation_command
                if actions or command:
                    break
                previous_response = response

        if not actions and not command:
            task.plan = plan
            task.plan_details = plan_details.model_dump(mode="json")
            task.validation_command = None
            task.verification_status = "pending"
            applied_actions = [
                item for item in task.actions
                if item.get("status") == "applied"
            ]
            previous_actions = (
                task.actions if task.tool_protocol_active else applied_actions
            )
            task.actions = []
            task.tool_protocol_active = False
            task.initialize_plan_task_history()
            if (
                TaskPlanner.request_expects_changes(task.task)
                and not previous_actions
            ):
                for row in _plan_task_rows(task):
                    if row.get("status") == "pending":
                        _record_plan_task_status(
                            task,
                            str(row["id"]),
                            "blocked",
                            "No file actions were proposed for this change request.",
                        )
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
            _assign_plan_action_records(
                task,
                plan_details,
                [],
                previous_actions,
            )
            task.add_step("plan", "completed", "Analysis plan prepared")
            _advance_plan_execution(task)
            return
        for action in actions:
            if action.operation == "create_file":
                if action.file_path in tools.list_project_files():
                    raise ValueError("Agent create_file action path already exists in the project.")
                continue
            target = tools.resolve_file(action.file_path)
            if tools.get_file_path(target) != action.file_path:
                raise ValueError("Agent action path does not match the project file.")
        sandbox_validation_result = None
        if actions and not task.tool_protocol_active:
            plan_details, actions, command = await _run_sandbox_plan_qa(
                task=task,
                tools=tools,
                context=context,
                response=response,
                plan_details=plan_details,
                actions=actions,
                command=command,
            )
            sandbox_validation_result = task.validation_result
            plan = [item.description for item in plan_details.tasks]
        task.plan = plan
        task.plan_details = plan_details.model_dump(mode="json")
        task.clarification_questions = []
        applied_actions = [
            item for item in task.actions
            if item.get("status") == "applied"
        ]
        previous_actions = (
            task.actions if task.tool_protocol_active else applied_actions
        )
        if len(previous_actions) + len(actions) > MAX_AGENT_ACTIONS:
            raise ValueError("The agent exceeded the maximum number of proposed changes.")
        _assign_plan_action_records(task, plan_details, actions, previous_actions)
        task.tool_protocol_active = False
        task.initialize_plan_task_history()
        task.validation_command = command
        task.validation_result = sandbox_validation_result
        task.verification_status = "pending"
        task.add_step("plan", "completed", "Implementation plan prepared")
        if actions:
            task.add_step("code_action", "completed", f"Prepared {len(actions)} change(s) for review")
        _advance_plan_execution(task)
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
    current_context_parts = [
        "PROJECT METADATA:\n"
        f"Name: {project.name}\n"
        f"Primary language: {project.language or 'unknown'}"
    ]
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


@router.post("/tasks/{task_id}/clarify", response_model=AgentTaskResponse)
def clarify_agent_task(
    task_id: str,
    request: AgentClarification,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user_id = cast(int, current_user.id)
    task = _get_owned_task(task_id, user_id, db)
    if task.status != "awaiting_clarification":
        raise HTTPException(
            status_code=409,
            detail="Task is not waiting for clarification.",
        )
    answer = request.answer.strip()
    if not answer:
        raise HTTPException(status_code=422, detail="Clarification answer cannot be empty.")
    if agent_tasks.transition(
        task.id,
        user_id,
        "awaiting_clarification",
        "planning",
    ) is None:
        raise HTTPException(
            status_code=409,
            detail="Task clarification is already being processed.",
        )
    task.clarification_history.append({
        "questions": "\n".join(task.clarification_questions),
        "answer": answer,
    })
    task.clarification_questions = []
    task.stop_reason = None
    task.add_step(
        "clarification",
        "completed",
        "User provided clarification",
        input=answer,
    )
    background_tasks.add_task(_plan_task, task.id, user_id)
    return task.as_dict()


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
        if task.plan_details is not None:
            _advance_plan_execution(task)
            task.persist()
            return task.as_dict()
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
        if task.plan_details is not None:
            _advance_plan_execution(task)
        elif any(item.get("status") == "awaiting_approval" for item in task.actions) or task.validation_command:
            task.status = "awaiting_approval"
            task.stop_reason = None
        elif any(item.get("status") == "failed" for item in task.actions):
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
    if _plan_validation_blockers(task):
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
        if task.plan_details is not None:
            task.validation_command = None
            task.validation_result = None
            _advance_plan_execution(task)
            task.stop_reason = (
                "Validation was skipped; plan tasks are complete but acceptance "
                "criteria remain not verified."
            )
        else:
            task.status = "completed"
            task.verification_status = "not_verified"
            task.stop_reason = "Validation was declined by the user."
            task.add_step("finish", "completed", task.stop_reason)
        task.persist()
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
        task.verification_status = (
            "verified"
            if safe_result.success
            else "not_verified"
            if not safe_result.executed
            else "failed"
        )
        if safe_result.success:
            _start_task_preview(task, db, user_id)
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
        if safe_result.success and task.plan_details is not None:
            task.add_step("test", "completed", "Validation passed", output=safe_result.stdout)
            _complete_plan_tasks_after_validation(task)
            if task.status == "completed":
                task.add_step("finish", "completed", "Agent task completed")
        elif safe_result.success:
            task.status = "completed"
            task.verification_status = "not_verified"
            task.stop_reason = (
                "The approved command passed, but no structured acceptance criteria "
                "were available for comparison."
            )
            task.add_step("test", "completed", "Validation passed", output=safe_result.stdout)
            task.add_step("finish", "completed", task.stop_reason)
        elif not safe_result.executed:
            task.verification_status = "not_verified"
            task.stop_reason = (
                "Validation was not run because Docker is unavailable; acceptance "
                "criteria remain not verified."
            )
            task.add_step(
                "test",
                "not_verified",
                "Validation was not executed",
                error=safe_result.stderr,
            )
            if task.plan_details is not None:
                _complete_plan_tasks_after_validation(task)
            else:
                task.status = "awaiting_validation"
        else:
            task.status = "failed"
            task.verification_status = "failed"
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
        task.verification_status = "not_verified"
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
        task.verification_status = "not_verified"
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
    if preview_service.stop(task_id):
        task.preview_url = None
        task.add_step(
            "preview",
            "completed",
            "Stopped the sandbox preview after task cancellation",
        )
    return task.as_dict()


@router.post("/tasks/{task_id}/preview/stop", response_model=AgentTaskResponse)
def stop_agent_preview(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = _get_owned_task(task_id, cast(int, current_user.id), db)
    if preview_service.stop(task_id):
        task.preview_url = None
        task.add_step("preview", "completed", "Stopped the sandbox preview")
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
        context = tools.retrieve_context(task.task)[:8000]
        if task.current_context:
            context = (
                f"CURRENT IDE CONTEXT:\n{task.current_context[:4000]}\n\n{context}"
            )
        context = (
            "REPOSITORY INTELLIGENCE (detected from safe project files):\n"
            f"{tools.project_intelligence()}\n\n{context}"
        )[:20000]
        prompt = (
            "Validation failed. Diagnose the failure and return a correction "
            "proposal only, using the same structured planning-result schema "
            "as the original task. Preserve the user's requirements and completed "
            "work. Focus only on the failed acceptance criteria and relevant "
            "files; do not re-propose completed changes unless they are necessary "
            "to fix the failure. Return status=clarification_required if a decision is "
            "materially ambiguous; otherwise return status=ready with a valid "
            "AgentPlan and complete CodeAction objects. For replace actions, "
            "copy exact start_line, end_line, old_code, and new_code from the "
            "provided project context. For new files, use operation=create_file "
            "and put full contents in new_code. Use only the existing agent "
            "validation allowlist. Use repository intelligence as file-derived "
            "evidence and do not claim candidates were executed or verified. "
            "Do not apply changes.\n\n"
            f"TASK:\n{task.task}\n\n"
            f"VALIDATION RESULT:\n{str(prior_result)[:12000]}"
        )
        prior_plan = task.plan_details or {}
        prior_plan_tasks = prior_plan.get("tasks", [])
        completed_tasks = (
            [
                row for row in prior_plan_tasks
                if isinstance(row, dict) and row.get("status") == "completed"
            ]
            if isinstance(prior_plan_tasks, list)
            else []
        )
        completed_history = [
            row for row in task.task_history
            if row.get("status") == "completed"
        ][-20:]
        applied_paths = []
        for item in task.actions:
            action_data = item.get("action")
            if (
                item.get("status") == "applied"
                and isinstance(action_data, dict)
                and isinstance(action_data.get("file_path"), str)
            ):
                applied_paths.append(action_data["file_path"])
        completed_work = json.dumps({
            "completed_plan_tasks": completed_tasks,
            "completed_task_history": completed_history,
            "already_applied_file_paths": sorted(set(applied_paths)),
        }, ensure_ascii=True)[:8000]
        prompt += f"\n\nCOMPLETED WORK TO PRESERVE:\n{completed_work}"
        if task.clarification_history:
            prompt += (
                "\n\nUSER CLARIFICATIONS:\n"
                + json.dumps(task.clarification_history[-10:], ensure_ascii=True)[:5000]
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
        existing_actions = [
            CodeAction.model_validate(item["action"])
            for item in task.actions
            if item.get("status") == "applied"
            and isinstance(item.get("action"), dict)
        ]
        for attempt in range(AI_AGENT_MAX_CORRECTION_RETRIES + 1):
            try:
                planning_result = TaskPlanner.parse_planning_result(
                    response,
                    existing_actions=existing_actions,
                )
                break
            except ValueError as error:
                if attempt >= AI_AGENT_MAX_CORRECTION_RETRIES:
                    raise
                retry_step = task.add_step(
                    "plan",
                    "running",
                    "Repairing invalid correction proposal",
                    input=(
                        f"Correction repair attempt {attempt + 1} of "
                        f"{AI_AGENT_MAX_CORRECTION_RETRIES}"
                    ),
                    error=str(error),
                )
                response = await ai_service.chat_json(
                    message=TaskPlanner.correction_repair_prompt(
                        TaskPlanner.include_clarifications(
                            task.task,
                            task.clarification_history,
                        ),
                        response,
                        str(error),
                        prior_result,
                        completed_work,
                    ),
                    context=context,
                    history=[],
                )
                retry_step.status = "completed"
                retry_step.output = response
                task.persist()
                if getattr(task, "status", None) == "cancelled":
                    return
        if planning_result.status == "clarification_required":
            task.clarification_questions = planning_result.clarification_questions
            task.validation_command = None
            task.actions = [
                item for item in task.actions
                if item.get("status") == "applied"
            ]
            task.status = "awaiting_clarification"
            task.stop_reason = "The agent needs clarification before it can prepare a safe correction."
            task.add_step(
                "clarification",
                "completed",
                "Waiting for user clarification",
                output="\n".join(task.clarification_questions),
            )
            task.persist()
            return
        assert planning_result.plan is not None
        plan = [item.description for item in planning_result.plan.tasks]
        actions = planning_result.actions
        command = planning_result.validation_command
        for action in actions:
            if action.operation == "create_file":
                if action.file_path in tools.list_project_files():
                    raise ValueError(
                        "Agent create_file action path already exists in the project."
                    )
                continue
            tools.resolve_file(action.file_path)
        task.plan = plan
        task.plan_details = planning_result.plan.model_dump(mode="json")
        task.clarification_questions = []
        prior_actions = [
            item for item in task.actions
            if item.get("status") == "applied"
        ]
        _assign_plan_action_records(
            task,
            planning_result.plan,
            actions,
            prior_actions,
        )
        task.initialize_plan_task_history()
        task.validation_command = command
        task.validation_result = None
        task.verification_status = "pending"
        task.add_step("analyze", "completed", "Prepared a bounded correction proposal")
        _advance_plan_execution(task)
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