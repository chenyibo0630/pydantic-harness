"""Demo sub-agent — a simple example to show the sub-agent pattern."""

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from sub_agents.demo_agent.config import Settings


def create_agent(settings: Settings) -> Agent[None, str]:
    return Agent(
        model=settings.model,
        instructions="You are a demo assistant. Respond briefly and cheerfully.",
        model_settings=ModelSettings(
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
        ),
        name="demo",
        defer_model_check=True,
    )
