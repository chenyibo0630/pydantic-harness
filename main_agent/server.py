"""Main Agent server — run with: uv run server.py"""

import logging
import logging.handlers
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add project root so shared packages (backend) are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.llm import build_model
from backend.core.memory import (
    EvictingMemory,
    InMemoryStore,
    SummarizingMemory,
)
from backend.core.sandbox import init_sandbox
from backend.core.skills import load_skills, init_skill_tool
from backend.gateway.routes import router
from main_agent.agent import build_system_prompt, create_agent
from main_agent.config import get_settings

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"


class _RenameUvicornError(logging.Formatter):
    """Display 'uvicorn.error' as 'uvicorn' — the name is a uvicorn legacy
    misnomer (it carries lifecycle/info messages, not just errors)."""

    def format(self, record: logging.LogRecord) -> str:
        if record.name == "uvicorn.error":
            record.name = "uvicorn"
        return super().format(record)


def _setup_logging(level: str) -> None:
    _LOG_DIR.mkdir(exist_ok=True)
    formatter = _RenameUvicornError(_LOG_FORMAT)

    root = logging.getLogger()
    root.setLevel(level)

    # console
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    # file — 10 MB per file, keep 5 backups
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "main_agent.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Override uvicorn's own logger handlers (they don't propagate to root)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for h in logging.getLogger(name).handlers:
            h.setFormatter(formatter)

    # Mute chatty third-party loggers so DEBUG mode stays focused on our
    # SSE event tracer and tool calls — HTTP libs dump full headers/bodies
    # at DEBUG, which drowns out the conversation timeline we care about.
    for name in (
        "httpx", "httpcore",
        "anthropic._base_client", "openai._base_client",
        "asyncio",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


class _SimpleRegistry:
    """Minimal registry wrapping a single agent."""

    def __init__(self, agent):
        self._agent = agent

    def get(self, name: str):
        if name != "main":
            raise KeyError(f"Unknown agent: {name!r}. Available: ['main']")
        return self._agent

    @property
    def available(self) -> list[str]:
        return ["main"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _setup_logging(settings.server.log_level)
    # skills/ is always at project root (parent of main_agent/)
    skills_dir = Path(__file__).resolve().parent.parent / "skills"
    skills = load_skills(skills_dir, enabled=settings.agent.skills or None)
    init_skill_tool(skills)
    if settings.agent.workspace or settings.sandbox.type == "remote":
        init_sandbox(
            settings.agent.workspace,
            skills_dir=str(skills_dir),
            skills=skills,
            sandbox_type=settings.sandbox.type,
            remote_url=settings.sandbox.remote_url,
            remote_token=settings.sandbox.token,
            remote_timeout=settings.sandbox.timeout,
        )
    agent = create_agent(settings, skills=skills)
    app.state.agent_registry = _SimpleRegistry(agent)
    model = build_model(settings.llm)
    base = InMemoryStore()
    app.state.memory = SummarizingMemory(EvictingMemory(base), model=model)
    app.state.build_system_prompt = lambda: build_system_prompt(settings, skills)
    app.state.stream_timeout = settings.server.stream_timeout
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="main-agent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "server:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=True,
    )
