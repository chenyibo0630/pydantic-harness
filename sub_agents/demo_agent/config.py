from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    model: str = Field(default="openai:gpt-4o")
    max_tokens: int = Field(default=512)
    temperature: float = Field(default=0.3)

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8001)
    cors_origins: list[str] = Field(default=["*"])
    stream_timeout: float = Field(default=60.0)
    log_level: str = Field(default="INFO")


@lru_cache
def get_settings() -> Settings:
    return Settings()
