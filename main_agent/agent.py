"""MainAgent — primary orchestrating agent."""

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from backend.core.llm import LLMConfig, build_model
from main_agent.config import Settings
from main_agent.tools.tools import get_available_tools


def _build_model_settings(llm: LLMConfig) -> ModelSettings:
    ms = ModelSettings(max_tokens=llm.max_tokens, temperature=llm.temperature)
    if llm.thinking is not None:
        ms["thinking"] = llm.thinking
    return ms


def create_agent(settings: Settings) -> Agent[None, str]:
    model = build_model(settings.llm)
    return Agent(
        model=model,
        instructions=settings.system_prompt,
        model_settings=_build_model_settings(settings.llm),
        tools=get_available_tools(),
        name="bob-harness",
    )
