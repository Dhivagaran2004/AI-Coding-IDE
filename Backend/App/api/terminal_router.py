import asyncio
import logging
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy.orm import Session

from App.auth.auth import get_current_user
from App.config import ENABLE_INTERACTIVE_TERMINAL, IDE_WORKSPACES_ROOT
from App.database.database import get_db
from App.models.project import Project
from App.models.user import User
from App.schema.terminal_schema import (
    TerminalCommandRequest,
    TerminalSessionCreate,
    TerminalSessionRename,
)

from App.service.Terminal.terminal_service import (
    TerminalService,
)
from App.service.workspace_paths import (
    WorkspacePathError,
    ensure_project_workspace,
)
from App.service.project_workspace_service import ProjectWorkspaceSyncError
from App.service.Terminal.terminal_sessions import (
    TerminalSessionError,
    TerminalSessionManager,
    TerminalSessionUnavailable,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from App.service.workspace_change_service import WorkspaceChangeManager


router = APIRouter(
    prefix="/projects",
    tags=["Terminal"],
)


terminal_service = TerminalService()
terminal_session_manager = TerminalSessionManager()
workspace_change_manager: "WorkspaceChangeManager | None" = None
logger = logging.getLogger(__name__)


def _require_owned_project(
    project_id: int,
    db: Session,
    current_user: User,
) -> Project:
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
    return project


def _ensure_project_workspace(project: Project, db: Session) -> None:
    previous_path = getattr(project, "workspace_path", None)
    try:
        ensure_project_workspace(
            project,
            workspace_root=terminal_service.workspace_root,
            legacy_roots=(
                None
                if terminal_service.workspace_root == IDE_WORKSPACES_ROOT
                else ()
            ),
        )
    except WorkspacePathError as error:
        raise HTTPException(
            status_code=500,
            detail="Project workspace could not be initialized.",
        ) from error
    if project.workspace_path != previous_path and hasattr(db, "commit"):
        db.commit()


def _require_interactive_terminal() -> None:
    if not ENABLE_INTERACTIVE_TERMINAL:
        raise HTTPException(
            status_code=503,
            detail=(
                "The interactive terminal is disabled. Set "
                "ENABLE_INTERACTIVE_TERMINAL=true to enable it."
            ),
        )


def stop_workspace_change_monitor() -> None:
    if workspace_change_manager is not None:
        workspace_change_manager.stop_all()


@router.post("/{project_id}/workspace/sync")
def sync_project_workspace_changes(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    global workspace_change_manager
    _require_interactive_terminal()
    project = _require_owned_project(project_id, db, current_user)
    _ensure_project_workspace(project, db)
    try:
        from App.service.workspace_change_service import (
            WorkspaceChangeService,
            workspace_change_manager as manager,
        )

        workspace_change_manager = manager
        return WorkspaceChangeService(
            db,
            change_manager=manager,
        ).sync_pending(project_id, current_user.id)
    except ImportError as error:
        raise HTTPException(
            status_code=503,
            detail=(
                "Install Backend/requirements-terminal.txt to enable "
                "workspace change monitoring."
            ),
        ) from error
    except ProjectWorkspaceSyncError as error:
        raise HTTPException(
            status_code=500,
            detail=f"Project workspace changes could not be synchronized: {error}",
        ) from error
    except OSError as error:
        raise HTTPException(
            status_code=503,
            detail="Unable to monitor the project workspace filesystem.",
        ) from error

@router.get("/{project_id}/terminal/sessions")
def list_terminal_sessions(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_interactive_terminal()
    _require_owned_project(project_id, db, current_user)
    return terminal_session_manager.list_sessions(
        user_id=current_user.id,
        project_id=project_id,
    )


@router.post("/{project_id}/terminal/sessions")
async def create_terminal_session(
    project_id: int,
    request: TerminalSessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_interactive_terminal()
    project = _require_owned_project(project_id, db, current_user)
    _ensure_project_workspace(project, db)
    try:
        session = await terminal_session_manager.create_session(
            user_id=current_user.id,
            project_id=project_id,
            cwd=Path(project.workspace_path),
            shell=request.shell,
            name=request.name,
        )
        ticket = terminal_session_manager.issue_ticket(
            session.id,
            user_id=current_user.id,
            project_id=project_id,
        )
    except TerminalSessionUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except TerminalSessionError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {**session.as_dict(), "ticket": ticket}


@router.post("/{project_id}/terminal/sessions/{session_id}/connect")
def connect_terminal_session(
    project_id: int,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_interactive_terminal()
    _require_owned_project(project_id, db, current_user)
    try:
        ticket = terminal_session_manager.issue_ticket(
            session_id,
            user_id=current_user.id,
            project_id=project_id,
        )
    except TerminalSessionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"ticket": ticket}


@router.put("/{project_id}/terminal/sessions/{session_id}")
def rename_terminal_session(
    project_id: int,
    session_id: str,
    request: TerminalSessionRename,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_interactive_terminal()
    _require_owned_project(project_id, db, current_user)
    try:
        return terminal_session_manager.rename_session(
            session_id,
            user_id=current_user.id,
            project_id=project_id,
            name=request.name,
        )
    except TerminalSessionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/{project_id}/terminal/sessions/{session_id}")
async def close_terminal_session(
    project_id: int,
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_interactive_terminal()
    _require_owned_project(project_id, db, current_user)
    try:
        await terminal_session_manager.close_session(
            session_id,
            user_id=current_user.id,
            project_id=project_id,
        )
    except TerminalSessionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"closed": True}


@router.websocket("/{project_id}/terminal/sessions/{session_id}/ws")
async def terminal_session_websocket(
    websocket: WebSocket,
    project_id: int,
    session_id: str,
    ticket: str = Query(min_length=32, max_length=128),
):
    if not ENABLE_INTERACTIVE_TERMINAL:
        await websocket.close(code=4403)
        return
    session = terminal_session_manager.consume_ticket(
        ticket,
        session_id=session_id,
        project_id=project_id,
    )
    if session is None:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    queue, backlog = await terminal_session_manager.attach(session, websocket)
    if backlog:
        await websocket.send_json({"type": "output", "data": backlog})
    if not session.process.is_alive():
        await websocket.close(code=1000)
        await terminal_session_manager.detach(session, websocket, queue)
        return

    async def send_output() -> None:
        while True:
            output = await queue.get()
            await websocket.send_json({"type": "output", "data": output})

    output_task = asyncio.create_task(send_output())
    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await websocket.close(code=1003)
                break
            message_type = message.get("type")
            try:
                if message_type == "input" and isinstance(message.get("data"), str):
                    await terminal_session_manager.send_input(
                        session,
                        message["data"],
                    )
                elif message_type == "resize":
                    columns = message.get("columns")
                    rows = message.get("rows")
                    if (
                        isinstance(columns, bool)
                        or not isinstance(columns, int)
                        or isinstance(rows, bool)
                        or not isinstance(rows, int)
                    ):
                        await websocket.close(code=1003)
                        break
                    await terminal_session_manager.resize(
                        session,
                        columns,
                        rows,
                    )
                elif message_type == "ping":
                    await websocket.send_json({"type": "pong"})
                else:
                    await websocket.close(code=1003)
                    break
            except TerminalSessionError as error:
                await websocket.send_json({
                    "type": "error",
                    "message": str(error),
                })
                await websocket.close(code=1008)
                break
    except WebSocketDisconnect:
        pass
    finally:
        output_task.cancel()
        await asyncio.gather(output_task, return_exceptions=True)
        await terminal_session_manager.detach(session, websocket, queue)


@router.post("/{project_id}/terminal/execute")
def execute_terminal_command(
    project_id: int,
    request: TerminalCommandRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Execute one allowlisted, read-only command inside a project workspace.
    """

    project = _require_owned_project(project_id, db, current_user)
    _ensure_project_workspace(project, db)
    try:

        result = terminal_service.execute_command(
            project_id=project_id,
            command=request.command,
        )

        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.success,
        }

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except FileNotFoundError as error:

        raise HTTPException(
            status_code=404,
            detail=str(error),
        )

    except Exception as error:

        logger.warning(
            "Terminal command failed for project %s (%s).",
            project_id,
            type(error).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Terminal command failed.",
        ) from error

# =========================================
# Stop Command
# =========================================

@router.post("/{project_id}/terminal/stop")
def stop_terminal_command(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):

    """
    Stop the currently running terminal command
    for a project.
    """

    project = _require_owned_project(project_id, db, current_user)
    _ensure_project_workspace(project, db)
    try:

        result = (
            terminal_service.stop_command(
                project_id=project_id
            )
        )

        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.success,
            "stopped": True,
        }

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception as error:

        logger.warning(
            "Terminal stop failed for project %s (%s).",
            project_id,
            type(error).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Terminal process could not be stopped.",
        ) from error
