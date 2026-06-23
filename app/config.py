"""
app/config.py
─────────────
Single source of truth for all environment-driven configuration.
Loaded once at startup; imported everywhere else as `from app.config import settings`.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────
    app_secret_key: str = "change-me"
    app_debug: bool = True
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    # ── Database ─────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./trusted_advisor.db"

    # ── Auth ─────────────────────────────────────────────────
    access_token_expire_minutes: int = 480

    # ── AI Provider ──────────────────────────────────────────
    llm_provider: Literal["claude", "openai"] = "claude"
    llm_model_claude: str = "claude-sonnet-4-6"
    llm_model_openai: str = "gpt-4o"

    anthropic_api_key: str = ""
    openai_api_key: str = ""

    @property
    def active_model(self) -> str:
        """Returns the model string for the currently active provider."""
        return (
            self.llm_model_claude
            if self.llm_provider == "claude"
            else self.llm_model_openai
        )


# Module-level singleton — import this everywhere
settings = Settings()
