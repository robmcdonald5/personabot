"""Shared test fixtures.

One session-scoped asyncpg pool is shared across every test. ``create_tables``
runs once at session setup with ``drop_first=True`` to wipe any leftover
schema. Per-test isolation uses an outer transaction + rollback — every
test sees a clean database without the cost of recreating tables.
"""

import os
from typing import AsyncIterator

import asyncpg
import pytest_asyncio

from personabot.db.manager import register_codecs
from personabot.db.models import create_tables

_DEFAULT_TEST_DSN = "postgresql://personabot:dev@localhost:5433/personabot"
_TEST_DSN = os.getenv("TEST_DATABASE_URL", _DEFAULT_TEST_DSN)


@pytest_asyncio.fixture(scope="session")
async def db_pool() -> AsyncIterator[asyncpg.Pool]:
    """Session-scoped asyncpg pool.

    ``drop_first=True`` wipes any leftover schema from a prior bot run
    against the same DB before recreating. DDL runs outside any
    transaction (asyncpg's simple protocol auto-commits) so the schema
    persists across subsequent per-test acquires.
    """
    pool = await asyncpg.create_pool(
        _TEST_DSN,
        min_size=1,
        max_size=5,
        init=register_codecs,
    )
    assert pool is not None
    try:
        async with pool.acquire() as raw:
            await create_tables(raw, drop_first=True)
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def db_connection(
    db_pool: asyncpg.Pool,
) -> AsyncIterator[asyncpg.Connection]:
    """Function-scoped connection with outer transaction + rollback.

    Acquires a pooled connection, opens an outer transaction, yields the
    raw ``asyncpg.Connection``, and rolls back on teardown. Tests run
    all DB ops on this connection; the rollback discards every change
    regardless of intermediate "commits" inside the tests.
    """
    async with db_pool.acquire() as raw:
        outer = raw.transaction()
        await outer.start()
        try:
            yield raw
        finally:
            await outer.rollback()
