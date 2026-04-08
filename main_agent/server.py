"""Main Agent server — run with: uv run server.py"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add project root so shared packages (backend) are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.gateway.routes import router
from main_agent.agent import create_agent
from main_agent.config import get_settings
from main_agent.tools.file import set_write_roots


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
    logging.basicConfig(level=settings.server.log_level)
    if settings.agent.workspace:
        set_write_roots([settings.agent.workspace])
    agent = create_agent(settings)
    app.state.agent_registry = _SimpleRegistry(agent)
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
