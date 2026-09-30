from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from App.auth.auth import get_current_user
from App.database.database import get_db
from App.models.project import Project
from App.models.user import User
from App.service.AI.index.repository_index_service import (
    RepositoryIndexService,
)


router = APIRouter(
    prefix="/projects",
    tags=["Repository Index"],
)


@router.post("/{project_id}/index")
def index_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
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
            status_code=404,
            detail="Project not found",
        )

    service = RepositoryIndexService(
        db=db,
        project_id=project_id,
    )
    return service.index_project()