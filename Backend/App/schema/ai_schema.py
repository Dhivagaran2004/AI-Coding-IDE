from typing import Literal

import re
from typing import Self

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)


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

    @field_validator("content")
    @classmethod
    def redact_message_secrets(cls, value: str) -> str:
        return _redact_secrets(value)


class SelectedCodeContext(BaseModel):
    file_path: str = Field(..., min_length=1, max_length=1000)
    language: str = Field(..., min_length=1, max_length=50)
    code: str = Field(..., min_length=1, max_length=20000)
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)

    @field_validator("code")
    @classmethod
    def redact_selected_code_secrets(cls, value: str) -> str:
        return _redact_secrets(value)

    @model_validator(mode="after")
    def validate_line_range(self):
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|"
    r"secret|password|passwd|credential|authorization|database_url)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s\"',;}\]]+)"
)
_ENVIRONMENT_LINE = re.compile(
    r"(?m)^\s*[A-Z_][A-Z0-9_]{1,}\s*=.*(?:\n|$)"
)
_BEARER_VALUE = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/-]+=*")
_RAW_CREDENTIAL = re.compile(
    r"\b(?:hf_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b"
)
_DATABASE_URL_PASSWORD = re.compile(
    r"(?i)((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^:/\s]+:)"
    r"[^@/\s]+(@)"
)
_JWT_VALUE = re.compile(
    r"\b[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)


def _redact_secrets(value: str) -> str:
    value = _BEARER_VALUE.sub(r"\1[REDACTED]", value)
    value = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", value)
    value = _DATABASE_URL_PASSWORD.sub(r"\1[REDACTED]\2", value)
    value = _RAW_CREDENTIAL.sub("[REDACTED]", value)
    value = _JWT_VALUE.sub("[REDACTED]", value)
    return _PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", value)


class TerminalContext(BaseModel):
    command: str = Field(..., min_length=1, max_length=2000)
    exit_code: int
    stdout: str = Field(default="", max_length=50000)
    stderr: str = Field(default="", max_length=50000)
    success: bool

    @field_validator("command")
    @classmethod
    def redact_command(cls, value: str) -> str:
        return _redact_secrets(value)

    @field_validator("stdout", "stderr")
    @classmethod
    def sanitize_output(cls, value: str) -> str:
        value = _ENVIRONMENT_LINE.sub("[environment variable omitted]\n", value)
        return _redact_secrets(value)[:12000]


class CodeAction(BaseModel):
    type: Literal["code_change"]
    operation: Literal["replace", "insert", "delete", "create_file"]
    file_path: str = Field(..., min_length=1, max_length=2000)
    description: str = Field(..., min_length=1, max_length=500)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    old_code: str = Field(default="", max_length=20000)
    new_code: str = Field(default="", max_length=20000)

    @model_validator(mode="after")
    def validate_replace_range(self) -> Self:
        if self.operation == "replace":
            if self.start_line is None or self.end_line is None:
                raise ValueError("replace actions require start_line and end_line")
            if self.end_line < self.start_line:
                raise ValueError("end_line must be greater than or equal to start_line")
            if "old_code" not in self.model_fields_set:
                raise ValueError("replace actions require old_code")
            if not self.old_code and (self.start_line, self.end_line) != (1, 1):
                raise ValueError("empty old_code is only valid at line 1")
        return self


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

    mode: Literal["ask", "plan", "edit"] = "ask"

    context: str | None = Field(
        default=None,
        max_length=50000,
        description="Optional code or additional IDE context.",
    )

    selected_code: SelectedCodeContext | None = Field(
        default=None,
        description="Optional bounded Monaco selection with source metadata.",
    )

    terminal_context: TerminalContext | None = Field(
        default=None,
        description="Optional latest terminal command result.",
    )

    history: list[AIMessage] = Field(
        default_factory=list,
        description="Previous messages in the conversation.",
    )

    project_id: int | None = Field(
    default=None,
    description=(
        "Optional project ID used to search "
        "repository context."
    ),
)
    conversation_id: int | None = Field(
        default=None,
        ge=1,
        description="Existing conversation to continue.",
    )

    @field_validator("message")
    @classmethod
    def redact_request_message_secrets(cls, value: str) -> str:
        return _redact_secrets(value)

    @field_validator("context")
    @classmethod
    def redact_request_context_secrets(cls, value: str | None) -> str | None:
        return _redact_secrets(value) if value is not None else None

    @model_validator(mode="after")
    def require_project_for_editor_context(self):
        if (
            self.project_id is None
            and (self.selected_code is not None or self.terminal_context is not None)
        ):
            raise ValueError(
                "project_id is required for selected-code or terminal context."
            )
        return self


class AIChatResponse(BaseModel):
    """
    Response model returned by the AI chat endpoint.
    """

    message: str
    provider: str
    model: str
    code_action: CodeAction | None = None
    plan: list[str] | None = None
    conversation_id: int | None = None
