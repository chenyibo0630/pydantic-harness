"""Request/Response models for the Sandbox Service API."""

from pydantic import BaseModel, Field


# ── Requests ────────────────────────────────────────────────────

class ExecuteCommandRequest(BaseModel):
    command: str
    workdir: str = "."
    timeout: int = Field(default=120, le=300)


class ReadFileRequest(BaseModel):
    path: str
    start_line: int = 0
    end_line: int = 0


class WriteFileRequest(BaseModel):
    path: str
    content: str
    append: bool = False


class StrReplaceRequest(BaseModel):
    path: str
    old_str: str
    new_str: str
    replace_all: bool = False


class ListDirRequest(BaseModel):
    path: str = "."
    max_depth: int = 2


class GlobFilesRequest(BaseModel):
    pattern: str
    path: str = "."


class GrepSearchRequest(BaseModel):
    pattern: str
    path: str = "."
    glob: str = ""
    context: int = 0


# ── Responses ───────────────────────────────────────────────────

class SandboxResponse(BaseModel):
    output: str


class SandboxErrorResponse(BaseModel):
    error: str
    code: str  # PATH_DENIED | FILE_NOT_FOUND | COMMAND_ERROR | TOOL_ERROR
