"""Application configuration using pydantic-settings."""

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain"})


def _is_local_dsn(dsn: str) -> bool:
    """True if ``dsn``'s host component resolves to the local machine.

    Covers named forms (``localhost``), IPv4 loopback (``127.0.0.0/8``),
    IPv6 loopback (``::1`` / ``[::1]``), and the unspecified address
    ``0.0.0.0`` — which a client library will typically route to local.
    """
    host = (urlparse(dsn).hostname or "").lower()
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_unspecified


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Note: pydantic-settings does NOT auto-load any .env file. All environment
    variables must be set via the real process environment (shell exports,
    Docker Compose env_file, systemd Environment=, etc). Removing .env
    auto-loading structurally prevents cross-environment mixups — e.g. a
    stray local `.env` being picked up when running against production.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
    )

    @field_validator("dev_guild_id", "healthcheck_url", mode="before")
    @classmethod
    def _empty_string_to_none(cls, v: object) -> object:
        """Coerce empty-string env values to None.

        Docker Compose's ``env_file:`` directive passes unset-looking
        variables to the container as the literal empty string, not as
        absent entries — so ``DEV_GUILD_ID=`` in ``.env.prod`` becomes
        ``""`` in the process environment, which fails ``int | None``
        parsing on ``dev_guild_id``. Coercing empty string to None here
        lets operators leave optional fields blank in their env files
        without sprinkling conditional logic throughout the stack.
        """
        if isinstance(v, str) and v == "":
            return None
        return v

    @model_validator(mode="after")
    def _no_production_on_localhost(self) -> "Settings":
        """Fail fast if a production build is pointed at a local database.

        A prod-shaped bot against a dev DB would skip the dev DROP+CREATE
        gate and silently write into whatever leftover schema the dev DB
        had. Rejecting this combination at Settings load time is the
        cheapest defense.

        Local prod-compose smoke tests reach Postgres via the Compose
        service name (``postgres``), not ``localhost``, so this guard
        doesn't interfere with them.
        """
        if self.is_production and _is_local_dsn(self.database_url):
            raise ValueError(
                "ENVIRONMENT=production + DATABASE_URL pointing at "
                "localhost is rejected as a cross-environment mixup "
                "guard. Either flip ENVIRONMENT=development or point "
                "DATABASE_URL at a real production host."
            )
        return self

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    # Required, no default. Gates dev-only behaviors like the DROP + CREATE
    # schema reset in db/models.py.
    environment: Literal["development", "production"]

    # Database — required. Full DSN, e.g.
    #   postgresql://personabot:dev@localhost:5433/personabot
    database_url: str

    # Discord
    discord_token: str
    dev_guild_id: int | None = None  # Set for instant command sync during development
    # Whether to auto-sync the command tree on startup. Guild sync (dev mode)
    # is cheap and runs regardless; global sync (production) is rate-limited
    # and discouraged for every restart — opt out and use a manual owner sync.
    auto_sync_commands: bool = True

    # Storage — all paths derived from data_dir. Media + export files live
    # on disk; Postgres owns row data.
    data_dir: Path = Path("data")

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    def export_path(self, guild_id: int, user_id: int) -> Path:
        """Canonical export JSONL path for a user in a guild."""
        return self.exports_dir / str(guild_id) / str(user_id) / "corpus.jsonl"

    # Scraping defaults
    default_limit: int = 10000

    # Pipeline / export parameters
    token_budget: int = 100000
    top_n_messages: int = 1000
    window_gap_hours: int = 4
    media_reaction_threshold: int = 2

    # Retention
    retention_hours: int = Field(default=24, ge=1)

    # Optional dead-man-switch URL (e.g. Healthchecks.io) — pinged at the
    # end of every successful retention_cleanup tick. Leave unset in dev.
    healthcheck_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()  # type: ignore[call-arg]
