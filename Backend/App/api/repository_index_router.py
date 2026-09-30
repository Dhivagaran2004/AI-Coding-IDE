from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from App.database.database import get_db
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
):
    """
    Index or update the repository index for a project.
    """

    service = RepositoryIndexService(
        db=db,
        project_id=project_id,
    )

    return service.index_project()