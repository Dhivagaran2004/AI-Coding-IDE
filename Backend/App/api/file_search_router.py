from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from App.database.database import get_db
from App.service.AI.search.file_search import (
    FileSearchService,
)


router = APIRouter(
    prefix="/projects",
    tags=["File Search"],
)


@router.get("/{project_id}/search")
def search_project_files(
    project_id: int,
    q: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="Search query",
    ),
    limit: int = Query(
        10,
        ge=1,
        le=50,
        description="Maximum number of results",
    ),
    db: Session = Depends(get_db),
):
    """
    Search files inside a project.
    """

    search_service = FileSearchService(
        db=db,
        project_id=project_id,
    )

    results = search_service.search(
        query=q,
        limit=limit,
    )

    return {
        "project_id": project_id,
        "query": q,
        "count": len(results),
        "results": [
            {
                "file_id": result.file_id,
                "name": result.name,
                "path": result.path,
                "content": result.content,
                "language": result.language,
                "score": result.score,
            }
            for result in results
        ],
    }