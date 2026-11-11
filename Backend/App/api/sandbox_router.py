import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from App.auth.auth import get_current_user
from App.database.database import get_db
from App.models.project import Project
from App.models.user import User
from App.schema.sandbox_schema import SandboxCommandRequest
from App.schema.ai_schema import TerminalContext
from App.service.project_workspace_service import (
    ProjectWorkspaceService,
    ProjectWorkspaceSyncError,
)
from App.service.sandbox_service import SandboxService

router = APIRouter(
    prefix="/projects",
    tags=["Sandbox"],
)

logger = logging.getLogger(__name__)

sandbox_service = SandboxService()


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


@router.post("/{project_id}/sandbox/execute")
def execute_sandbox_command(
    project_id: int,
    request: SandboxCommandRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_owned_project(project_id, db, current_user)

    try:
        result, _ = ProjectWorkspaceService(
            db,
            sandbox_service=sandbox_service,
        ).execute_command(
            project_id=project_id,
            user_id=current_user.id,
            command=request.command,
            timeout=request.timeout,
        )
        response = TerminalContext(
            command=request.command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            success=result.success,
            timeout=result.timeout,
            created_files=result.created_files,
            modified_files=result.modified_files,
            deleted_files=result.deleted_files,
        )
        return response.model_dump(exclude={"command"})
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ProjectWorkspaceSyncError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        logger.warning(
            "Sandbox command failed for project %s (%s).",
            project_id,
            type(error).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Sandbox command failed.",
        ) from error
