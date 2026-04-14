"""Tests for the production-vs-localhost model validator in Settings."""

import pytest

from personabot.config import Settings

_FAKE_DISCORD = "fake-token"


def test_production_rejects_localhost() -> None:
    """ENVIRONMENT=production + DATABASE_URL containing 'localhost' raises."""
    with pytest.raises(ValueError, match="cross-environment mixup guard"):
        Settings(
            discord_token=_FAKE_DISCORD,
            environment="production",
            database_url="postgresql://p:pw@localhost:5432/p",
        )


def test_production_rejects_127_0_0_1() -> None:
    """ENVIRONMENT=production + DATABASE_URL containing '127.0.0.1' raises."""
    with pytest.raises(ValueError, match="cross-environment mixup guard"):
        Settings(
            discord_token=_FAKE_DISCORD,
            environment="production",
            database_url="postgresql://p:pw@127.0.0.1:5432/p",
        )


def test_production_accepts_non_localhost_hostname() -> None:
    """ENVIRONMENT=production + real hostname passes (e.g. Compose service name)."""
    s = Settings(
        discord_token=_FAKE_DISCORD,
        environment="production",
        database_url="postgresql://p:pw@postgres:5432/p",
    )
    assert s.environment == "production"


def test_development_accepts_localhost() -> None:
    """ENVIRONMENT=development + localhost is the normal dev flow."""
    s = Settings(
        discord_token=_FAKE_DISCORD,
        environment="development",
        database_url="postgresql://p:dev@localhost:5433/p",
    )
    assert s.environment == "development"


def test_healthcheck_url_defaults_to_none() -> None:
    """New healthcheck_url field is optional with None default."""
    s = Settings(
        discord_token=_FAKE_DISCORD,
        environment="development",
        database_url="postgresql://p:dev@localhost:5433/p",
    )
    assert s.healthcheck_url is None


def test_empty_string_dev_guild_id_coerced_to_none() -> None:
    """DEV_GUILD_ID='' from a Compose env_file is treated as unset.

    Docker Compose's env_file directive passes blank lines as empty
    strings to the container, which would otherwise fail int | None
    parsing on dev_guild_id. The before-validator in Settings coerces
    them to None so operators can leave the field blank in .env.prod.
    """
    s = Settings(
        discord_token=_FAKE_DISCORD,
        environment="development",
        database_url="postgresql://p:dev@localhost:5433/p",
        dev_guild_id="",  # type: ignore[arg-type]
    )
    assert s.dev_guild_id is None


def test_empty_string_healthcheck_url_coerced_to_none() -> None:
    """HEALTHCHECK_URL='' is also coerced — same env_file gotcha."""
    s = Settings(
        discord_token=_FAKE_DISCORD,
        environment="development",
        database_url="postgresql://p:dev@localhost:5433/p",
        healthcheck_url="",
    )
    assert s.healthcheck_url is None
