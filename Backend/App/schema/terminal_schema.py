from pydantic import BaseModel, Field


class TerminalCommandRequest(BaseModel):
    command: str = Field(..., max_length=2000)