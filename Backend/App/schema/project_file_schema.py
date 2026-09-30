from typing import Optional, List
from datetime import datetime

from pydantic import BaseModel


class ProjectFileBase(BaseModel):
    name: str
    type: str
    content: Optional[str] = None
    language: Optional[str] = None
    parent_id: Optional[int] = None


class ProjectFileCreate(ProjectFileBase):
    pass


class ProjectFileUpdate(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None
    language: Optional[str] = None
    parent_id: Optional[int] = None


class ProjectFileResponse(ProjectFileBase):
    id: int
    project_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectFileTree(BaseModel):
    id: int
    project_id: Optional[int] = None
    parent_id: Optional[int] = None
    name: str
    type: str
    path: Optional[str] = None
    content: Optional[str] = None
    language: Optional[str] = None
    children: List["ProjectFileTree"] = []

    class Config:
        from_attributes = True


ProjectFileTree.model_rebuild()