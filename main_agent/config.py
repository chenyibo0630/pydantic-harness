from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from backend.core.llm import LLMConfig


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


class Settings(BaseModel):
    llm: LLMConfig = LLMConfig()
    server: ServerConfig = ServerConfig()
    agent: AgentConfig = AgentConfig()

    @property
    def system_prompt(self) -> str:
        path = Path(__file__).parent / self.agent.system_prompt_file
        return path.read_text(encoding="utf-8").strip()


@lru_cache
def get_settings() -> Settings:
    if _CONFIG_PATH.exists():
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return Settings(**raw)
    return Settings()
