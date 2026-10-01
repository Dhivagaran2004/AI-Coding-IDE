from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from App.auth.auth import get_current_user
from App.database.database import get_db
from App.models.project import Project
from App.models.project_file import ProjectFile
from App.models.user import User

from App.schema.terminal_schema import (
    TerminalCommandRequest,
    TerminalExecutionResponse,
    TerminalRunRequest,
)

from App.service.Terminal.code_execution_service import (
    CodeExecutionService,
    ExecutionQueueFull,
    ProjectFileNotFound,
    SandboxUnavailable,
    UnsupportedSourceFile,
)


router = APIRouter(
    prefix="/projects",
    tags=["Terminal"],
)


code_execution_service = CodeExecutionService()


def require_owned_project(
    project_id: int,
    current_user: User,
    db: Session,
) -> None:
    project = (
        db.query(Project)
        .filter(
            Project.id == project_id,
            Project.user_id == current_user.id,
        )
        .first()
    )
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )


@router.post("/{project_id}/terminal/execute")
def execute_terminal_command(
    project_id: int,
    request: TerminalCommandRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Execute a shell command inside a project workspace.
    """

    require_owned_project(project_id, current_user, db)
    if not request.command.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Command cannot be empty.",
        )
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Host shell execution is disabled. "
            "Use the sandboxed /terminal/run endpoint."
        ),
    )


@router.post(
    "/{project_id}/terminal/run",
    response_model=TerminalExecutionResponse,
)
def run_project_file(
    project_id: int,
    request: TerminalRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_owned_project(project_id, current_user, db)
    project_files = (
        db.query(ProjectFile)
        .filter(ProjectFile.project_id == project_id)
        .all()
    )

    try:
        return code_execution_service.execute_file(
            project_id=project_id,
            project_files=project_files,
            file_id=request.file_id,
            code=request.code,
        )
    except ProjectFileNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except UnsupportedSourceFile as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    except ExecutionQueueFull as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(error),
        ) from error
    except SandboxUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sandbox execution failed.",
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

    require_owned_project(project_id, current_user, db)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Host shell processes are no longer available.",
    )
