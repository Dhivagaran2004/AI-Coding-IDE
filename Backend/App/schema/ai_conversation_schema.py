from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AIConversationCreate(BaseModel):
    project_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        if not title:
            raise ValueError("Conversation title cannot be empty.")
        return title


class AIConversationRename(BaseModel):
    title: str = Field(..., min_length=1, max_length=160)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("Conversation title cannot be empty.")
        return title


class AIConversationSummary(BaseModel):
    id: int
    project_id: int | None
    user_id: int
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AIConversationMessageResponse(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    metadata: dict[str, object] = Field(
        default_factory=dict,
        validation_alias="message_metadata",
    )
    created_at: datetime


class AIConversationDetail(AIConversationSummary):
    messages: list[AIConversationMessageResponse]
