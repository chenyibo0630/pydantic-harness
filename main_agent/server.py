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

from backend.core.memory import InMemoryStore
from backend.core.sandbox import init_sandbox
from backend.gateway.routes import router
from main_agent.agent import create_agent
from main_agent.config import get_settings

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"


def _setup_logging(level: str) -> None:
    _LOG_DIR.mkdir(exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)

    # console
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(console)

    # file — 10 MB per file, keep 5 backups
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "main_agent.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(file_handler)


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
    if settings.agent.workspace:
        init_sandbox(settings.agent.workspace)
    agent = create_agent(settings)
    app.state.agent_registry = _SimpleRegistry(agent)
    app.state.memory = InMemoryStore()
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
