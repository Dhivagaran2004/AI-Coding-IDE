from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from App.api.terminal_router import terminal_service
from App.auth.auth import get_current_user
from App.database.database import get_db
from App.models.project import Project
from App.models.user import User
from App.service.git_service import GitCommandError, GitService


router = APIRouter(prefix="/projects", tags=["Git"])


def _git_service(project_id: int, user_id: int, db: Session) -> GitService:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.user_id == user_id)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    try:
        directory = terminal_service.validate_project_directory(project_id)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise HTTPException(status_code=404, detail="Project workspace not found.") from error
    return GitService(directory)


@router.get("/{project_id}/git/status")
def get_git_status(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _git_service(project_id, current_user.id, db)
    try:
        return service.status()
    except GitCommandError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/{project_id}/git/diff")
def get_git_diff(
    project_id: int,
    staged: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = _git_service(project_id, current_user.id, db)
    try:
        return service.diff(staged=staged)
    except GitCommandError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
