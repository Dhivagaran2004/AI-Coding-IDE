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
from App.service.AI.index.repository_index_service import (
    RepositoryIndexService,
)
from App.service.AI.index.vector_rag_service import VectorRAGService
from App.service.project_workspace_service import (
    ProjectWorkspaceConflictError,
    ProjectWorkspaceService,
    ProjectWorkspaceSyncError,
)

from App.schema.project_file_schema import (
    ProjectFileCreate,
    ProjectFileUpdate,
    ProjectFileResponse,
    ProjectFileTree
)
from App.schema.ai_schema import CodeAction
from App.service.AI.code_action_service import (
    CodeActionService,
    PatchNotFoundError,
    PatchValidationError,
    StalePatchError,
    UnsupportedPatchOperation,
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


def _project_file_path(db: Session, project_file: ProjectFile) -> str:
    return ProjectWorkspaceService(db)._get_project_path(project_file)


def _sync_workspace_file(
    db: Session,
    project_id: int,
    user_id: int,
    file_path: str,
    content: str | None,
    *,
    expected_content: str | None = None,
    check_expected: bool = False,
) -> None:
    try:
        ProjectWorkspaceService(db).sync_file(
            project_id=project_id,
            user_id=user_id,
            file_path=file_path,
            content=content,
            expected_content=expected_content,
            check_expected=check_expected,
        )
    except ProjectWorkspaceConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ProjectWorkspaceSyncError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Project file changed in the database but workspace sync failed: {error}",
        ) from error
#===============================================================

def validate_parent(
    project_id: int,
    parent_id: int,
    db: Session
):

    parent = (
        db.query(ProjectFile)
        .filter(
            ProjectFile.id == parent_id,
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

    return parent
 
#===============================================================

def validate_move(
    project_id: int,
    file_id: int,
    new_parent_id: int,
    db: Session
):

    # Cannot be its own parent
    if file_id == new_parent_id:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An item cannot be its own parent"
        )

    # Get destination
    new_parent = (
        db.query(ProjectFile)
        .filter(
            ProjectFile.id == new_parent_id,
            ProjectFile.project_id == project_id
        )
        .first()
    )

    if new_parent is None:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Destination folder not found"
        )

    # Destination must be folder
    if new_parent.type != "folder":

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Destination must be a folder"
        )

    # Get current item
    current_item = (
        db.query(ProjectFile)
        .filter(
            ProjectFile.id == file_id,
            ProjectFile.project_id == project_id
        )
        .first()
    )

    if current_item is None:

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File or folder not found"
        )

    # Files cannot create folder cycles
    if current_item.type != "folder":
        return

    # Walk upward through parents
    current_parent_id = new_parent_id

    while current_parent_id is not None:

        if current_parent_id == file_id:

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot move a folder inside "
                    "itself or its child"
                )
            )

        parent = (
            db.query(ProjectFile)
            .filter(
                ProjectFile.id == current_parent_id,
                ProjectFile.project_id == project_id
            )
            .first()
        )

        if parent is None:
            break

        current_parent_id = parent.parent_id


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
        parent_path = (
            _project_file_path(db, parent)
            if file_data.parent_id is not None
            else ""
        )
        new_path = (
            f"{parent_path}/{new_item.name}"
            if parent_path
            else new_item.name
        )
        _assert_workspace_file_matches(
            db,
            project_id,
            current_user.id,
            new_path,
            None,
            expect_absent=True,
        )

    # 6. Save
    db.add(new_item)
    db.commit()
    db.refresh(new_item)

    if new_item.type == "file":
        _sync_workspace_file(
            db,
            project_id,
            current_user.id,
            _project_file_path(db, new_item),
            new_item.content or "",
            check_expected=True,
        )
        RepositoryIndexService(
            db=db,
            project_id=project_id,
        ).index_file_after_save(new_item)
    else:
        _sync_workspace_folder(
            db,
            project_id,
            current_user.id,
            _project_file_path(db, new_item),
        )

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
    if (
        file_data.content is not None
        and file_data.expected_content is not None
        and project_file.content != file_data.expected_content
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The file changed in the workspace. Reload it before saving.",
        )
    old_path = (
        _project_file_path(db, project_file)
        if project_file.type == "file"
        else None
    )
    old_content = project_file.content or ""
    if (
        project_file.type == "file"
        and (file_data.content is not None or file_data.name is not None or file_data.parent_id is not None)
        and old_path is not None
    ):
        _assert_workspace_file_matches(
            db,
            project_id,
            current_user.id,
            old_path,
            old_content,
        )
    old_folder_path = (
        _project_file_path(db, project_file)
        if project_file.type == "folder"
        else None
    )
    folder_path_changed = (
        project_file.type == "folder"
        and (file_data.name is not None or file_data.parent_id is not None)
    )
    descendant_files = []
    if folder_path_changed and old_folder_path is not None:
        descendant_files = [
            item
            for item in db.query(ProjectFile)
            .filter(
                ProjectFile.project_id == project_id,
                ProjectFile.type == "file",
            )
            .all()
            if _project_file_path(db, item).startswith(old_folder_path + "/")
        ]
        for item in descendant_files:
            _assert_workspace_file_matches(
                db,
                project_id,
                current_user.id,
                _project_file_path(db, item),
                item.content or "",
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

    content_was_updated = file_data.content is not None

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

    if project_file.type == "file":
        new_path = _project_file_path(db, project_file)
        if old_path is not None and old_path != new_path:
            _sync_workspace_file(
                db,
                project_id,
                current_user.id,
                old_path,
                None,
                expected_content=old_content,
                check_expected=True,
            )
        if content_was_updated or old_path != new_path:
            _sync_workspace_file(
                db,
                project_id,
                current_user.id,
                new_path,
                project_file.content or "",
                expected_content=old_content if old_path == new_path else None,
                check_expected=True,
            )
    elif folder_path_changed and old_folder_path is not None:
        new_folder_path = _project_file_path(db, project_file)
        if old_folder_path != new_folder_path:
            for item in descendant_files:
                old_item_path = _project_file_path(
                    db,
                    item,
                ).replace(
                    new_folder_path,
                    old_folder_path,
                    1,
                )
                new_item_path = _project_file_path(db, item)
                item_content = item.content or ""
                _sync_workspace_file(
                    db,
                    project_id,
                    current_user.id,
                    old_item_path,
                    None,
                    expected_content=item_content,
                    check_expected=True,
                )
                _sync_workspace_file(
                    db,
                    project_id,
                    current_user.id,
                    new_item_path,
                    item.content or "",
                    check_expected=True,
                )
            _sync_workspace_folder(
                db,
                project_id,
                current_user.id,
                old_folder_path,
                delete=True,
            )
            _sync_workspace_folder(
                db,
                project_id,
                current_user.id,
                new_folder_path,
            )

    if content_was_updated:
        index_service = RepositoryIndexService(
            db=db,
            project_id=project_id,
        )
        index_service.index_file_after_save(
            project_file,
            only_if_needed=True,
        )
    elif project_file.type == "file":
        VectorRAGService(
            db=db,
            project_id=project_id,
        ).ensure_file_indexed(project_file)

    return project_file


@router.post(
    "/{file_id}/patch",
    response_model=ProjectFileResponse,
)
def apply_project_file_patch(
    project_id: int,
    file_id: int,
    action: CodeAction,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        updated_file = CodeActionService(db).apply_action(
            project_id=project_id,
            file_id=file_id,
            user_id=current_user.id,
            action=action,
        )
        _sync_workspace_file(
            db,
            project_id,
            current_user.id,
            _project_file_path(db, updated_file),
            updated_file.content or "",
        )
        return updated_file
    except PatchNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except StalePatchError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except UnsupportedPatchOperation as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    except PatchValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error


def _assert_workspace_file_matches(
    db: Session,
    project_id: int,
    user_id: int,
    file_path: str,
    expected_content: str | None,
    *,
    expect_absent: bool = False,
) -> None:
    try:
        ProjectWorkspaceService(db).assert_workspace_file_matches(
            project_id=project_id,
            user_id=user_id,
            file_path=file_path,
            expected_content=expected_content,
            expect_absent=expect_absent,
        )
    except ProjectWorkspaceSyncError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


def _sync_workspace_folder(
    db: Session,
    project_id: int,
    user_id: int,
    folder_path: str,
    *,
    delete: bool = False,
) -> None:
    try:
        ProjectWorkspaceService(db).sync_folder(
            project_id=project_id,
            user_id=user_id,
            folder_path=folder_path,
            delete=delete,
        )
    except ProjectWorkspaceSyncError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Project folder changed in the database but workspace sync failed: {error}",
        ) from error


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
    deleted_folder_path = (
        _project_file_path(db, project_file)
        if project_file.type == "folder"
        else None
    )
    deleted_file_paths: list[tuple[str, str]] = []

    project_files = (
        db.query(ProjectFile)
        .filter(ProjectFile.project_id == project_id)
        .all()
    )
    children_by_parent: dict[int, list[ProjectFile]] = {}
    for item in project_files:
        if item.parent_id is not None:
            children_by_parent.setdefault(item.parent_id, []).append(item)

    file_ids = []
    pending_ids = [project_file.id]
    while pending_ids:
        current_id = pending_ids.pop()
        current_item = next(
            (item for item in project_files if item.id == current_id),
            None,
        )
        if current_item is not None and current_item.type == "file":
            file_ids.append(current_id)
            path = _project_file_path(db, current_item)
            content = current_item.content or ""
            _assert_workspace_file_matches(
                db,
                project_id,
                current_user.id,
                path,
                content,
            )
            deleted_file_paths.append((path, content))
        pending_ids.extend(
            child.id for child in children_by_parent.get(current_id, [])
        )

    VectorRAGService(
        db=db,
        project_id=project_id,
    ).delete_files(file_ids)

    db.delete(project_file)
    db.commit()

    for file_path, content in deleted_file_paths:
        _sync_workspace_file(
            db,
            project_id,
            current_user.id,
            file_path,
            None,
            expected_content=content,
            check_expected=True,
        )
    if deleted_folder_path is not None:
        _sync_workspace_folder(
            db,
            project_id,
            current_user.id,
            deleted_folder_path,
            delete=True,
        )

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

    def assign_paths(node: ProjectFileTree, parent_path: str = ""):
        node.path = f"{parent_path}/{node.name}".lstrip("/")
        for child in node.children:
            assign_paths(child, node.path)

    for root in roots:
        assign_paths(root)

    return roots
