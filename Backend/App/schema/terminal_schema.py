from pydantic import BaseModel, Field


class TerminalCommandRequest(BaseModel):
    command: str    


class TerminalRunRequest(BaseModel):
    file_id: int = Field(gt=0)
    code: str = Field(min_length=1, max_length=100000)


class TerminalExecutionResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    success: bool