"""
Application configuration for the PCP AI Form Builder backend.

All settings are read from environment variables (or a local .env file) via
pydantic-settings. This keeps secrets (LLM API keys) out of the codebase.

The active LLM provider is selected by `LLM_PROVIDER`; each provider's own
settings are namespaced and only read by that provider's adapter.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---------------------------------------------------------------
    app_name: str = "PCP AI Form Builder"
    app_version: str = "0.1.0"
    debug: bool = False

    # --- LLM provider selection -------------------------------------------
    # azure_openai | openai | anthropic | bedrock | ollama | ...
    llm_provider: str = "anthropic"

    # Bounded repair loop: max number of repair attempts before bailing out.
    max_repair_attempts: int = 2

    # --- Azure OpenAI provider settings (read only by that adapter) --------
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"

    # --- Anthropic (Claude) provider settings (read only by that adapter) --
    # Drop these into .env when credentials are available; no code change needed.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    anthropic_api_version: str = "2023-06-01"
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_max_tokens: int = 8000

    # --- CORS --------------------------------------------------------------
    # Comma-separated list of allowed origins (mirrors the extractor service).
    cors_allow_origins: str = (
        "http://localhost:3000,"
        "http://localhost:5173,"
        "https://digi.punjab.gov.in"
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse the comma-separated CORS origins into a list."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (read once per process)."""
    return Settings()
