from typing import Literal

from pydantic import BaseModel, Field


class TerminalCommandRequest(BaseModel):
    command: str = Field(..., max_length=2000)


class TerminalSessionCreate(BaseModel):
    shell: Literal["powershell", "cmd", "bash"]
    name: str | None = Field(default=None, max_length=80)


class TerminalSessionRename(BaseModel):
    name: str = Field(min_length=1, max_length=80)