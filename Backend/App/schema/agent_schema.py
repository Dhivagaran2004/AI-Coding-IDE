from typing import Literal

from pydantic import BaseModel, Field

from App.schema.ai_schema import CodeAction, SelectedCodeContext, TerminalContext


class AgentTaskCreate(BaseModel):
    project_id: int = Field(..., gt=0)
    task: str = Field(..., min_length=1, max_length=5000)
    context: str | None = Field(default=None, max_length=30000)
    current_file_path: str | None = Field(default=None, max_length=2000)
    selected_code: SelectedCodeContext | None = None
    terminal_context: TerminalContext | None = None


class AgentApproval(BaseModel):
    action_indexes: list[int] = Field(default_factory=list, max_length=10)
    reject_indexes: list[int] = Field(default_factory=list, max_length=10)
    accept_all: bool = False
    reject_all: bool = False


class AgentCommandApproval(BaseModel):
    command: str = Field(..., min_length=1, max_length=500)
    approved: bool


class AgentAction(BaseModel):
    action: CodeAction
    status: Literal["awaiting_approval", "applied", "rejected", "failed"]
    error: str | None = None


class AgentChangeResult(BaseModel):
    path: str
    content: str
    file_id: int


class AgentTaskResponse(BaseModel):
    id: str
    project_id: int
    task: str
    mode: Literal["agent"]
    status: Literal[
        "pending",
        "planning",
        "awaiting_approval",
        "executing",
        "validating",
        "failed",
        "completed",
        "cancelled",
    ]
    plan: list[str]
    actions: list[AgentAction]
    validation_command: str | None = None
    validation_result: dict[str, object] | None = None
    change_history: list[dict[str, object]]
    iteration: int
    steps: list[dict[str, object]]
    stop_reason: str | None = None
    created_at: str
    updated_at: str
    changes: list[AgentChangeResult] = Field(default_factory=list)