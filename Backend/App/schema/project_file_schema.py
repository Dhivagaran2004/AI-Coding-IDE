from typing import List, Optional
from datetime import datetime

from pydantic import BaseModel, Field


class ProjectFileCreate(BaseModel):
	name: str
	type: str
	content: Optional[str] = None
	language: Optional[str] = None
	parent_id: Optional[int] = None


class ProjectFileUpdate(BaseModel):
	name: Optional[str] = None
	content: Optional[str] = None
	language: Optional[str] = None
	parent_id: Optional[int] = None


class ProjectFileResponse(BaseModel):
	id: int
	project_id: int
	parent_id: Optional[int] = None
	name: str
	type: str
	content: Optional[str] = None
	language: Optional[str] = None
	created_at: datetime
	updated_at: datetime

	class Config:
		from_attributes = True


class ProjectFileTree(BaseModel):
	id: int
	name: str
	type: str
	children: List["ProjectFileTree"] = Field(default_factory=list)

