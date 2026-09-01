from pydantic import BaseModel


class TerminalCommandRequest(BaseModel):
    command: str    