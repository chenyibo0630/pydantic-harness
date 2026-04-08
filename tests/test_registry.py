import pytest
from pydantic_ai import Agent

from backend.core.llm import LLMConfig
from main_agent.config import Settings
from main_agent.agent import create_agent
from main_agent.server import _SimpleRegistry


def _make_registry():
    settings = Settings(llm=LLMConfig(type="openai", model="test"))
    agent = create_agent(settings)
    return _SimpleRegistry(agent)


def test_get_main_agent():
    assert isinstance(_make_registry().get("main"), Agent)


def test_get_unknown_agent():
    with pytest.raises(KeyError, match="Unknown agent"):
        _make_registry().get("nonexistent")


def test_available_agents():
    assert "main" in _make_registry().available


def test_singleton_identity():
    r = _make_registry()
    assert r.get("main") is r.get("main")
