"""
Provider factory: selects and builds the active LLMProvider from config.

The rest of the app calls `get_provider()` and receives whatever adapter the
`LLM_PROVIDER` env var selects. Switching provider = change env + restart, with
no feature-code change (plan section 4.2).
"""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.providers.anthropic import AnthropicProvider
from app.providers.azure_openai import AzureOpenAIProvider
from app.providers.base import LLMError, LLMProvider


def build_provider() -> LLMProvider:
    """Construct the provider named by settings.llm_provider."""
    settings = get_settings()
    provider_name = settings.llm_provider.lower().strip()

    if provider_name == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            api_version=settings.anthropic_api_version,
            base_url=settings.anthropic_base_url,
            max_tokens=settings.anthropic_max_tokens,
        )

    if provider_name == "azure_openai":
        return AzureOpenAIProvider(
            endpoint=settings.azure_openai_endpoint,
            deployment=settings.azure_openai_deployment,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )

    # Future adapters (openai, bedrock, ollama, ...) slot in here.
    raise LLMError(
        f"Unknown or unconfigured LLM_PROVIDER: '{settings.llm_provider}'. "
        "Supported: anthropic, azure_openai."
    )


@lru_cache
def get_provider() -> LLMProvider:
    """Return a cached provider instance (built once per process)."""
    return build_provider()
