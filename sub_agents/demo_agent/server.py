"""Demo Agent server — run with: uv run server.py"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add project root so shared packages (backend, skills) are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.gateway.routes import router
from sub_agents.demo_agent.agent import create_agent
from sub_agents.demo_agent.config import get_settings


class _SimpleRegistry:
    """Minimal registry wrapping a single agent."""

    def __init__(self, agent):
        self._agent = agent

    def get(self, name: str):
        if name != "demo":
            raise KeyError(f"Unknown agent: {name!r}. Available: ['demo']")
        return self._agent

    @property
    def available(self) -> list[str]:
        return ["demo"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    agent = create_agent(settings)
    app.state.agent_registry = _SimpleRegistry(agent)
    app.state.stream_timeout = settings.stream_timeout
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="demo-agent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
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
        host=settings.host,
        port=settings.port,
        reload=True,
    )
