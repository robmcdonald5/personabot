"""Application configuration using pydantic-settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Discord
    discord_token: str
    dev_guild_id: int | None = None  # Set for instant command sync during development

    # Storage — all paths derived from data_dir
    data_dir: Path = Path("data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "personabot.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    # Scraping defaults
    default_limit: int = 10000

    # Pipeline / export parameters
    token_budget: int = 100000
    top_n_messages: int = 1000
    window_gap_hours: int = 4
    media_reaction_threshold: int = 2

    # Retention
    retention_hours: int = Field(default=24, ge=1)


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()  # type: ignore[call-arg]
