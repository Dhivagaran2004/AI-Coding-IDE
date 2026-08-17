from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, ConfigDict


# =========================================
# Create File / Folder
# =========================================

class ProjectFileCreate(BaseModel):

    name: str = Field(
        ...,
        min_length=1,
        max_length=255
    )

    type: str = Field(
        ...,
        pattern="^(file|folder)$"
    )

    parent_id: Optional[int] = None

    content: Optional[str] = None

    language: Optional[str] = Field(
        default=None,
        max_length=50
    )


# =========================================
# Update File / Folder
# =========================================

class ProjectFileUpdate(BaseModel):

    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255
    )

    content: Optional[str] = None

    language: Optional[str] = Field(
        default=None,
        max_length=50
    )

    parent_id: Optional[int] = None


# =========================================
# Basic Response
# =========================================

class ProjectFileResponse(BaseModel):

    id: int

    project_id: int

    parent_id: Optional[int]

    name: str

    type: str

    content: Optional[str]

    language: Optional[str]

    created_at: datetime

    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )


# =========================================
# File Tree Response
# =========================================

class ProjectFileTree(BaseModel):

    id: int

    name: str

    type: str

    children: List["ProjectFileTree"] = []

    model_config = ConfigDict(
        from_attributes=True
    )