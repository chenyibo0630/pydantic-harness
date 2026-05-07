"""LLM model factory — maps LLMConfig to pydantic-ai Model instances."""

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.alibaba import AlibabaProvider
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.azure import AzureProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider

from backend.core.llm.config import LLMConfig


def build_model(config: LLMConfig) -> Model:
    """Build a pydantic-ai Model from LLMConfig."""
    match config.type:
        case "azure":
            provider = AzureProvider(
                azure_endpoint=config.azure_endpoint,
                api_key=config.api_key,
                api_version=config.azure_api_version,
            )
            return OpenAIChatModel(config.model, provider=provider)

        case "openai":
            kwargs = {"api_key": config.api_key} if config.api_key else {}
            provider = OpenAIProvider(**kwargs)
            return OpenAIChatModel(config.model, provider=provider)

        case "deepseek":
            kwargs = {"api_key": config.api_key} if config.api_key else {}
            provider = DeepSeekProvider(**kwargs)
            return OpenAIChatModel(config.model, provider=provider)

        case "qwen":
            kwargs: dict = {}
            if config.api_key:
                kwargs["api_key"] = config.api_key
            if config.base_url:
                kwargs["base_url"] = config.base_url
            provider = AlibabaProvider(**kwargs)
            return OpenAIChatModel(config.model, provider=provider)

        case "anthropic":
            # Native Anthropic API — uses AnthropicModel, not OpenAIChatModel.
            kwargs: dict = {}
            if config.api_key:
                kwargs["api_key"] = config.api_key
            if config.base_url:
                kwargs["base_url"] = config.base_url
            provider = AnthropicProvider(**kwargs)
            return AnthropicModel(config.model, provider=provider)
