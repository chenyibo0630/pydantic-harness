from backend.core.llm import LLMConfig
from main_agent.config import Settings, ServerConfig


def test_defaults():
    s = Settings()
    assert s.llm.max_tokens == 4096
    assert s.llm.temperature == 0
    assert s.server.port == 8000


def test_override():
    s = Settings(
        llm=LLMConfig(type="openai", model="gpt-4o", temperature=0.5),
        server=ServerConfig(port=9000),
    )
    assert s.llm.type == "openai"
    assert s.server.port == 9000


def test_system_prompt_loads():
    s = Settings()
    prompt = s.system_prompt
    assert len(prompt) > 0
