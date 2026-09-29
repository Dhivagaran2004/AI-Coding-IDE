from pydantic import BaseModel
from typing import Optional, Literal
from datetime import datetime


# =========================================================
# CREATE FILE / FOLDER
# =========================================================

class ProjectFileCreate(BaseModel):
    name: str
    type: Literal["file", "folder"]
    parent_id: Optional[int] = None
    content: Optional[str] = None
    language: Optional[str] = None


# =========================================================
# UPDATE FILE / FOLDER
# =========================================================

class ProjectFileUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    content: Optional[str] = None
    language: Optional[str] = None


# =========================================================
# RESPONSE
# =========================================================

class ProjectFileResponse(BaseModel):
    id: int
    project_id: int
    parent_id: Optional[int] = None
    name: str
    type: Literal["file", "folder"]
    content: Optional[str] = None
    language: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# =========================================================
# FILE TREE
# =========================================================

class ProjectFileTree(BaseModel):
    id: int
    name: str
    type: Literal["file", "folder"]
    children: list["ProjectFileTree"] = []

    class Config:
        from_attributes = True
