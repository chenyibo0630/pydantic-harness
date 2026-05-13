import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

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


class SandboxConfig(BaseModel):
    type: Literal["local", "remote"] = "local"
    remote_url: str = "http://sandbox:8100"
    token: str = ""
    timeout: int = 60
    # Sandbox-side: explicit opt-in to run without a Bearer token. Refused
    # at sandbox startup unless either `token` is set OR `allow_no_auth` is
    # True. Never enable in production.
    allow_no_auth: bool = False


class AgentConfig(BaseModel):
    system_prompt_file: str = "prompts/SYSTEM.md"
    workspace: str = ""
    skills: list[str] = Field(default_factory=list)
    # Directory holding the agent's curated long-term memory
    # (MEMORY.md / USER.md). Empty → defaults to ``main_agent/prompts/``
    # alongside SYSTEM.md / SOUL.md (load_prompts skips MEMORY/USER files).
    memory_dir: str = ""
    # Directory for per-conversation persistent state (message history,
    # evicted tool result cache, system prompt snapshot). Empty → defaults
    # to ``/data/.session`` in docker, ``./.session`` for local dev.
    session_dir: str = ""


class Settings(BaseModel):
    llm: LLMConfig = LLMConfig()
    server: ServerConfig = ServerConfig()
    agent: AgentConfig = AgentConfig()
    sandbox: SandboxConfig = SandboxConfig()
    @property
    def system_prompt(self) -> str:
        prompts_dir = Path(__file__).parent / "prompts"
        main_file = Path(self.agent.system_prompt_file).name
        return load_prompts(prompts_dir, main_file=main_file)


@lru_cache
def get_settings() -> Settings:
    if _CONFIG_PATH.exists():
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    else:
        raw = {}

    settings = Settings(**raw)

    # Allow env vars to override sandbox config (for Docker)
    if env_type := os.environ.get("SANDBOX_TYPE"):
        settings.sandbox.type = env_type  # type: ignore[assignment]
    if env_url := os.environ.get("SANDBOX_REMOTE_URL"):
        settings.sandbox.remote_url = env_url
    if env_token := os.environ.get("SANDBOX_TOKEN"):
        settings.sandbox.token = env_token

    return settings
