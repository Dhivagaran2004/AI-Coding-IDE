from typing import Literal

from pydantic import BaseModel, Field


class AIMessage(BaseModel):
    """
    A single message in an AI conversation.
    """

    role: Literal["system", "user", "assistant"]
    content: str = Field(
        ...,
        min_length=1,
        max_length=50000,
    )


class AIChatRequest(BaseModel):
    """
    Request model for AI chat.

    Supports both a new user message and optional
    conversation history.
    """

    message: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Current message sent by the user.",
    )

    context: str | None = Field(
        default=None,
        max_length=50000,
        description="Optional code or additional IDE context.",
    )

    history: list[AIMessage] = Field(
        default_factory=list,
        description="Previous messages in the conversation.",
    )


class AIChatResponse(BaseModel):
    """
    Response model returned by the AI chat endpoint.
    """

    message: str
    provider: str
    model: str