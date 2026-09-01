from pydantic import BaseModel, Field


class AIChatRequest(BaseModel):
    """
    Request model for AI chat.
    """

    message: str = Field(
        ...,
        min_length=1,
        description="Message sent by the user to the AI assistant.",
    )

    context: str | None = Field(
        default=None,
        description="Optional code or additional context provided to the AI.",
    )


class AIChatResponse(BaseModel):
    """
    Response model returned by the AI chat endpoint.
    """

    message: str