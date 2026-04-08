import pytest
from httpx import ASGITransport, AsyncClient

from backend.core.llm import LLMConfig
from main_agent.config import Settings
from main_agent.server import create_app, _SimpleRegistry
from main_agent.agent import create_agent


@pytest.fixture
def settings() -> Settings:
    return Settings(llm=LLMConfig(type="openai", model="test"))


@pytest.fixture
def app(settings):
    test_app = create_app()
    agent = create_agent(settings)
    test_app.state.agent_registry = _SimpleRegistry(agent)
    test_app.state.stream_timeout = settings.server.stream_timeout
    return test_app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
