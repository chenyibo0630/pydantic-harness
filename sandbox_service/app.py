"""Sandbox Service — standalone FastAPI server wrapping LocalSandbox.

Exposes the Sandbox ABC as HTTP endpoints. Runs as a separate container.
"""

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.core.sandbox.exceptions import (
    CommandError,
    FileNotFoundError_,
    PathDeniedError,
    ToolError,
)
from backend.core.sandbox.local import LocalSandbox
from backend.core.skills import load_skills
from sandbox_service.schemas import (
    ExecuteCommandRequest,
    GlobFilesRequest,
    GrepSearchRequest,
    ListDirRequest,
    ReadFileRequest,
    SandboxErrorResponse,
    SandboxResponse,
    StrReplaceRequest,
    WriteFileRequest,
)

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"


class _RenameUvicornError(logging.Formatter):
    """Display 'uvicorn.error' as 'uvicorn' — the name is a uvicorn legacy
    misnomer (it carries lifecycle/info messages, not just errors)."""

    def format(self, record: logging.LogRecord) -> str:
        if record.name == "uvicorn.error":
            record.name = "uvicorn"
        return super().format(record)


def _setup_logging() -> None:
    """Apply timestamped format to root logger and uvicorn's handlers.

    Uvicorn configures its own ('uvicorn', 'uvicorn.error', 'uvicorn.access')
    loggers before importing the app, so we override their formatters here
    instead of relying on log propagation.
    """
    formatter = _RenameUvicornError(_LOG_FORMAT)
    level = os.environ.get("SANDBOX_LOG_LEVEL", "INFO").upper()

    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
    else:
        for h in root.handlers:
            h.setFormatter(formatter)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for h in logging.getLogger(name).handlers:
            h.setFormatter(formatter)


_setup_logging()
logger = logging.getLogger(__name__)

_SANDBOX_TOKEN = os.environ.get("SANDBOX_TOKEN", "")


def _verify_token(request: Request) -> None:
    """Validate Bearer token on every request."""
    if not _SANDBOX_TOKEN:
        return  # no token configured — allow (dev mode)
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {_SANDBOX_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    token = os.environ.get("SANDBOX_TOKEN", "")
    allow_no_auth = os.environ.get("SANDBOX_ALLOW_NO_AUTH", "").lower() == "true"
    if not token and not allow_no_auth:
        raise RuntimeError(
            "SANDBOX_TOKEN not set. Refusing to start an unauthenticated sandbox. "
            "For local dev, set SANDBOX_ALLOW_NO_AUTH=true explicitly."
        )
    if not token and allow_no_auth:
        logger.warning(
            "Sandbox running WITHOUT authentication (SANDBOX_ALLOW_NO_AUTH=true). "
            "Do not use in production."
        )

    workspace = os.environ.get("SANDBOX_WORKSPACE", "/app/workspace")
    skills_dir = os.environ.get("SANDBOX_SKILLS_DIR", "/app/skills")
    skills = load_skills(skills_dir)
    sandbox = LocalSandbox(
        workspace,
        skills_dir=skills_dir,
        skills=skills,
    )
    app.state.sandbox = sandbox
    logger.info("Sandbox initialized: workspace=%s, skills=%d", workspace, len(skills))
    yield


app = FastAPI(title="sandbox-service", version="0.1.0", lifespan=lifespan)


def _get_sandbox(request: Request) -> LocalSandbox:
    return request.app.state.sandbox


def _handle_tool_error(exc: ToolError) -> JSONResponse:
    """Map ToolError subclasses to HTTP status codes."""
    status = 400
    if isinstance(exc, CommandError):
        status = 500
    body = SandboxErrorResponse(error=str(exc), code=exc.code)
    return JSONResponse(status_code=status, content=body.model_dump())


# ── Health ──────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Sandbox Endpoints ───────────────────────────────────────────

@app.post("/sandbox/execute_command", response_model=SandboxResponse, dependencies=[Depends(_verify_token)])
async def execute_command(body: ExecuteCommandRequest, sandbox: LocalSandbox = Depends(_get_sandbox)):
    try:
        output = sandbox.execute_command(body.command, body.workdir, body.timeout)
        return SandboxResponse(output=output)
    except ToolError as exc:
        return _handle_tool_error(exc)


@app.post("/sandbox/read_file", response_model=SandboxResponse, dependencies=[Depends(_verify_token)])
async def read_file(body: ReadFileRequest, sandbox: LocalSandbox = Depends(_get_sandbox)):
    try:
        output = sandbox.read_file(body.path, body.start_line, body.end_line)
        return SandboxResponse(output=output)
    except ToolError as exc:
        return _handle_tool_error(exc)


@app.post("/sandbox/write_file", response_model=SandboxResponse, dependencies=[Depends(_verify_token)])
async def write_file(body: WriteFileRequest, sandbox: LocalSandbox = Depends(_get_sandbox)):
    try:
        output = sandbox.write_file(body.path, body.content, body.append)
        return SandboxResponse(output=output)
    except ToolError as exc:
        return _handle_tool_error(exc)


@app.post("/sandbox/str_replace", response_model=SandboxResponse, dependencies=[Depends(_verify_token)])
async def str_replace(body: StrReplaceRequest, sandbox: LocalSandbox = Depends(_get_sandbox)):
    try:
        output = sandbox.str_replace(body.path, body.old_str, body.new_str, body.replace_all)
        return SandboxResponse(output=output)
    except ToolError as exc:
        return _handle_tool_error(exc)


@app.post("/sandbox/list_dir", response_model=SandboxResponse, dependencies=[Depends(_verify_token)])
async def list_dir(body: ListDirRequest, sandbox: LocalSandbox = Depends(_get_sandbox)):
    try:
        output = sandbox.list_dir(body.path, body.max_depth)
        return SandboxResponse(output=output)
    except ToolError as exc:
        return _handle_tool_error(exc)


@app.post("/sandbox/glob_files", response_model=SandboxResponse, dependencies=[Depends(_verify_token)])
async def glob_files(body: GlobFilesRequest, sandbox: LocalSandbox = Depends(_get_sandbox)):
    try:
        output = sandbox.glob_files(body.pattern, body.path)
        return SandboxResponse(output=output)
    except ToolError as exc:
        return _handle_tool_error(exc)


@app.post("/sandbox/grep_search", response_model=SandboxResponse, dependencies=[Depends(_verify_token)])
async def grep_search(body: GrepSearchRequest, sandbox: LocalSandbox = Depends(_get_sandbox)):
    try:
        output = sandbox.grep_search(body.pattern, body.path, body.glob, body.context)
        return SandboxResponse(output=output)
    except ToolError as exc:
        return _handle_tool_error(exc)


if __name__ == "__main__":
    uvicorn.run("sandbox_service.app:app", host="0.0.0.0", port=8100, reload=True)
