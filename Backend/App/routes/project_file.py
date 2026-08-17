from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status
)

from sqlalchemy.orm import Session

from App.database.database import get_db
from App.auth.auth import get_current_user

from App.models.user import User
from App.models.project import Project
from App.models.project_file import ProjectFile

from App.schema.project_file_schema import (
    ProjectFileCreate,
    ProjectFileUpdate,
    ProjectFileResponse,
    ProjectFileTree
)


router = APIRouter(
    prefix="/projects/{project_id}/files",
    tags=["Project Files"]
)


# =========================================================
# Helper: Check Project Ownership
# =========================================================

def get_user_project(
    project_id: int,
    current_user: User,
    db: Session
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
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    return project


# =========================================================
# Helper: Get File / Folder
# =========================================================

def get_project_file(
    project_id: int,
    file_id: int,
    db: Session
):

    project_file = (
        db.query(ProjectFile)
        .filter(
            ProjectFile.id == file_id,
            ProjectFile.project_id == project_id
        )
        .first()
    )

    if project_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File or folder not found"
        )

    return project_file


# =========================================================
# CREATE FILE / FOLDER
# =========================================================

@router.post(
    "",
    response_model=ProjectFileResponse,
    status_code=status.HTTP_201_CREATED
)
def create_file_or_folder(
    project_id: int,
    file_data: ProjectFileCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):

    # 1. Check project ownership
    get_user_project(
        project_id,
        current_user,
        db
    )

    # 2. Validate parent
    if file_data.parent_id is not None:

        parent = (
            db.query(ProjectFile)
            .filter(
                ProjectFile.id == file_data.parent_id,
                ProjectFile.project_id == project_id
            )
            .first()
        )

        if parent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent folder not found"
            )

        if parent.type != "folder":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent must be a folder"
            )

    # 3. Check duplicate name
    existing = (
        db.query(ProjectFile)
        .filter(
            ProjectFile.project_id == project_id,
            ProjectFile.parent_id == file_data.parent_id,
            ProjectFile.name == file_data.name
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A file or folder with this name already exists"
        )

    # 4. Folder-specific validation
    if file_data.type == "folder":

        new_item = ProjectFile(
            project_id=project_id,
            parent_id=file_data.parent_id,
            name=file_data.name,
            type="folder",
            content=None,
            language=None
        )

    # 5. File-specific validation
    else:

        new_item = ProjectFile(
            project_id=project_id,
            parent_id=file_data.parent_id,
            name=file_data.name,
            type="file",
            content=file_data.content or "",
            language=file_data.language
        )

    # 6. Save
    db.add(new_item)
    db.commit()
    db.refresh(new_item)

    return new_item


# =========================================================
# LIST FILES / FOLDERS
# =========================================================

@router.get(
    "",
    response_model=list[ProjectFileResponse]
)
def list_project_files(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):

    # Check ownership
    get_user_project(
        project_id,
        current_user,
        db
    )

    files = (
        db.query(ProjectFile)
        .filter(
            ProjectFile.project_id == project_id
        )
        .order_by(
            ProjectFile.type.desc(),
            ProjectFile.name.asc()
        )
        .all()
    )

    return files


# =========================================================
# GET SINGLE FILE / FOLDER
# =========================================================

@router.get(
    "/{file_id}",
    response_model=ProjectFileResponse
)
def get_single_project_file(
    project_id: int,
    file_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):

    # Check project ownership
    get_user_project(
        project_id,
        current_user,
        db
    )

    return get_project_file(
        project_id,
        file_id,
        db
    )


# =========================================================
# UPDATE FILE / FOLDER
# =========================================================

@router.put(
    "/{file_id}",
    response_model=ProjectFileResponse
)
def update_project_file(
    project_id: int,
    file_id: int,
    file_data: ProjectFileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):

    # Check project ownership
    get_user_project(
        project_id,
        current_user,
        db
    )

    # Get item
    project_file = get_project_file(
        project_id,
        file_id,
        db
    )

    # Update name
    if file_data.name is not None:

        existing = (
            db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == project_id,
                ProjectFile.parent_id == project_file.parent_id,
                ProjectFile.name == file_data.name,
                ProjectFile.id != file_id
            )
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A file or folder with this name already exists"
            )

        project_file.name = file_data.name

    # Update content
    if file_data.content is not None:

        if project_file.type != "file":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Folders cannot contain file content"
            )

        project_file.content = file_data.content

    # Update language
    if file_data.language is not None:

        if project_file.type != "file":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Folders cannot have a programming language"
            )

        project_file.language = file_data.language

    # Move item
    if file_data.parent_id is not None:

        if file_data.parent_id == file_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An item cannot be its own parent"
            )

        new_parent = (
            db.query(ProjectFile)
            .filter(
                ProjectFile.id == file_data.parent_id,
                ProjectFile.project_id == project_id,
                ProjectFile.type == "folder"
            )
            .first()
        )

        if new_parent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Destination folder not found"
            )

        project_file.parent_id = file_data.parent_id

    db.commit()
    db.refresh(project_file)

    return project_file


# =========================================================
# DELETE FILE / FOLDER
# =========================================================

@router.delete(
    "/{file_id}"
)
def delete_project_file(
    project_id: int,
    file_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):

    # Check ownership
    get_user_project(
        project_id,
        current_user,
        db
    )

    # Get item
    project_file = get_project_file(
        project_id,
        file_id,
        db
    )

    item_name = project_file.name

    db.delete(project_file)
    db.commit()

    return {
        "message": "File or folder deleted successfully",
        "name": item_name
    }


# =========================================================
# FILE TREE
# =========================================================

@router.get(
    "/tree/all",
    response_model=list[ProjectFileTree]
)
def get_project_file_tree(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):

    # Check ownership
    get_user_project(
        project_id,
        current_user,
        db
    )

    all_items = (
        db.query(ProjectFile)
        .filter(
            ProjectFile.project_id == project_id
        )
        .all()
    )

    item_map = {}

    # Create tree nodes
    for item in all_items:

        item_map[item.id] = ProjectFileTree(
            id=item.id,
            name=item.name,
            type=item.type,
            children=[]
        )

    roots = []

    # Build hierarchy
    for item in all_items:

        node = item_map[item.id]

        if item.parent_id is None:

            roots.append(node)

        else:

            parent_node = item_map.get(
                item.parent_id
            )

            if parent_node:

                parent_node.children.append(node)

    return roots