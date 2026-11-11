from pydantic import BaseModel, Field


class SandboxCommandRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=2000)
    timeout: int | None = Field(default=30, ge=1, le=120)
