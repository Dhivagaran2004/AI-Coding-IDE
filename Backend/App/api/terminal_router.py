import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from App.auth.auth import get_current_user
from App.database.database import get_db
from App.models.project import Project
from App.models.user import User
from App.schema.terminal_schema import (
    TerminalCommandRequest,
)

from App.service.Terminal.terminal_service import (
    TerminalService,
)


router = APIRouter(
    prefix="/projects",
    tags=["Terminal"],
)


terminal_service = TerminalService()
logger = logging.getLogger(__name__)


def _require_owned_project(
    project_id: int,
    db: Session,
    current_user: User,
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
        raise HTTPException(status_code=404, detail="Project not found.")


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

    _require_owned_project(project_id, db, current_user)
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

    _require_owned_project(project_id, db, current_user)
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
