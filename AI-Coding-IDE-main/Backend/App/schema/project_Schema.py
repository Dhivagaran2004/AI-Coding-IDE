from pydantic import BaseModel
from typing import Optional


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    language: str


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None


class ProjectResponse(BaseModel):
    id: int
    user_id: int
    name: str
    description: Optional[str] = None
    language: str

    class Config:
        from_attributes = True

