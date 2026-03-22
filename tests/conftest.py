"""Shared test fixtures."""

import aiosqlite
import pytest
import pytest_asyncio

from personabot.config import Settings, get_settings
from personabot.db.models import create_tables


@pytest.fixture
def test_settings() -> Settings:
    """Settings with fake credentials for testing."""
    get_settings.cache_clear()
    return Settings(
        discord_token="fake-token",
    )


@pytest_asyncio.fixture
async def db_connection():
    """In-memory SQLite database with schema applied."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    await create_tables(db)
    yield db
    await db.close()
