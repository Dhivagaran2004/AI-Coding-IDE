from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from App.database.database import get_db
from App.models.project import Project
from App.models.user import User
from App.schema.project_Schema import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse
)
from App.auth.auth import get_current_user


router = APIRouter(
    prefix="/projects",
    tags=["Projects"]
)


# -----------------------------------------
# Create Project
# -----------------------------------------

@router.post(
    "/",
    response_model=ProjectResponse
)
def create_project(
    project: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):

    new_project = Project(
        user_id=current_user.id,
        name=project.name,
        description=project.description,
        language=project.language
    )

    db.add(new_project)
    db.commit()
    db.refresh(new_project)

    return new_project


# -----------------------------------------
# Get My Projects
# -----------------------------------------

@router.get(
    "/",
    response_model=list[ProjectResponse]
)
def get_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):

    projects = (
        db.query(Project)
        .filter(Project.user_id == current_user.id)
        .all()
    )

    return projects

# -----------------------------------------
# Update Project
# -----------------------------------------

@router.put(
    "/{project_id}",
    response_model=ProjectResponse
)
def update_project(
    project_id: int,
    project_data: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):

    project = (
        db.query(Project)
        .filter(
            Project.id == project_id,
            Project.user_id == current_user.id
        )
        .first()
    )

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    if project_data.name is not None:
        project.name = project_data.name

    if project_data.description is not None:
        project.description = project_data.description

    if project_data.language is not None:
        project.language = project_data.language

    db.commit()
    db.refresh(project)

    return project

# -----------------------------------------
# Delete Project
# -----------------------------------------

@router.delete(
    "/{project_id}"
)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):

    project = (
        db.query(Project)
        .filter(
            Project.id == project_id,
            Project.user_id == current_user.id
        )
        .first()
    )

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found"
        )

    db.delete(project)

    db.commit()

    return {
        "message": "Project deleted successfully"
    }