from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from backend.core.llm import LLMConfig
from backend.core.prompt import load_prompts


_CONFIG_PATH = Path(__file__).parent / "config.yaml"


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default=["*"])
    stream_timeout: float = 120.0
    log_level: str = "INFO"


class AgentConfig(BaseModel):
    system_prompt_file: str = "prompts/SYSTEM.md"
    workspace: str = ""
    skills: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    llm: LLMConfig = LLMConfig()
    server: ServerConfig = ServerConfig()
    agent: AgentConfig = AgentConfig()
    @property
    def system_prompt(self) -> str:
        prompts_dir = Path(__file__).parent / "prompts"
        main_file = Path(self.agent.system_prompt_file).name
        return load_prompts(prompts_dir, main_file=main_file)


@lru_cache
def get_settings() -> Settings:
    if _CONFIG_PATH.exists():
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return Settings(**raw)
    return Settings()
