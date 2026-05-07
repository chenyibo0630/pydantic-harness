"""LLM configuration — shared across all agents."""

from typing import Literal

from pydantic import BaseModel, Field


LLMType = Literal["openai", "azure", "deepseek", "qwen", "anthropic"]
ThinkingLevel = Literal["minimal", "low", "medium", "high", "xhigh"]


class LLMConfig(BaseModel):
    type: LLMType = "openai"
    model: str = Field(default="gpt-4o", description="Model name / deployment name")
    api_key: str = ""
    temperature: float = 0
    max_tokens: int = 4096
    thinking: ThinkingLevel | bool | None = None

    # Azure specific
    azure_endpoint: str = ""
    azure_api_version: str = "2024-12-01-preview"

    # DeepSeek / Qwen: base_url override (optional)
    base_url: str = ""
