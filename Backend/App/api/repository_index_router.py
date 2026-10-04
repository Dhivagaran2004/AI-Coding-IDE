import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from App.auth.auth import get_current_user
from App.database.database import SessionLocal, get_db
from App.models.project import Project
from App.models.user import User
from App.service.AI.index.repository_index_service import (
    IndexingAlreadyRunning,
    RepositoryIndexService,
)


router = APIRouter(
    prefix="/projects",
    tags=["Repository Index"],
)

logger = logging.getLogger(__name__)


def _run_background_index(project_id: int) -> None:
    db = SessionLocal()
    try:
        RepositoryIndexService(db=db, project_id=project_id).index_project(
            queued=True
        )
    except Exception as exc:
        logger.warning(
            "Background repository indexing failed for project %s (%s).",
            project_id,
            type(exc).__name__,
        )
    finally:
        db.close()


@router.post("/{project_id}/index")
def index_project(
    project_id: int,
    background_tasks: BackgroundTasks,
    background: bool = False,
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
    try:
        if background:
            state = service.queue_job()
            background_tasks.add_task(_run_background_index, project_id)
            return state
        return service.index_project()
    except IndexingAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail="Repository indexing is already pending or running.",
        ) from exc


@router.get("/{project_id}/index/status")
def get_indexing_status(
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
        raise HTTPException(status_code=404, detail="Project not found")
    return RepositoryIndexService(db=db, project_id=project_id).get_job_state()