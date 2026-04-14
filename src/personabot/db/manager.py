"""Database connection lifecycle management (asyncpg pool owner).

This module owns the single ``asyncpg.Pool`` for the bot process. Cogs
acquire via ``bot.db_conn()`` which delegates here. The pool is sized
for a single-process Discord bot (``min=2, max=10``).

In ``environment="development"`` ``connect()`` drops and recreates the
schema on every startup. Production skips that path — dbmate migrations
own the schema via the ``migrate`` init container in
``docker-compose.prod.yml``.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from personabot.db.models import create_tables

logger = logging.getLogger(__name__)

_POOL_MIN_SIZE = 2
_POOL_MAX_SIZE = 10

# Timeout on pool shutdown. Docker's default stop-grace is 10 seconds.
_POOL_CLOSE_TIMEOUT = 10.0


async def _register_codecs(conn: asyncpg.Connection) -> None:
    """Install the JSONB codec on a freshly-pooled connection.

    Without this, asyncpg treats JSONB columns as ``str`` — query
    functions would have to ``json.dumps``/``json.loads`` by hand. With
    the codec, ``list[int] <-> jsonb`` is automatic.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


class DatabaseManager:
    """Owns the asyncpg connection pool for the bot process."""

    def __init__(self, database_url: str, *, reset_schema_on_connect: bool) -> None:
        self.database_url = database_url
        self._reset_schema_on_connect = reset_schema_on_connect
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        """Return the active pool, raising if not connected."""
        if self._pool is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._pool

    async def connect(self) -> None:
        """Create the pool and optionally drop + recreate the schema."""
        self._pool = await asyncpg.create_pool(
            self.database_url,
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            init=_register_codecs,
        )
        logger.info(
            "Database pool created (min=%d max=%d)",
            _POOL_MIN_SIZE,
            _POOL_MAX_SIZE,
        )

        if self._reset_schema_on_connect:
            async with self._pool.acquire() as raw:
                await create_tables(raw, drop_first=True)
            logger.info("Schema reset: all tables dropped and recreated")

    async def close(self) -> None:
        """Close the pool with a hard timeout.

        The timeout guards against a leaked acquire (a cog forgets to
        exit a ``db_conn()`` context manager) so Docker's 10-second
        stop-grace doesn't get wedged.
        """
        if self._pool is None:
            return
        try:
            await asyncio.wait_for(self._pool.close(), timeout=_POOL_CLOSE_TIMEOUT)
            logger.info("Database pool closed")
        except asyncio.TimeoutError:
            logger.error(
                "Pool close timed out after %.0fs — a connection leak "
                "somewhere is pinning a pool slot",
                _POOL_CLOSE_TIMEOUT,
            )
            self._pool.terminate()
        finally:
            self._pool = None

    @asynccontextmanager
    async def db_conn(self) -> AsyncIterator[asyncpg.Connection]:
        """Acquire a pooled connection. Cog-facing shortcut::

        async with bot.db_conn() as db, db.transaction():
            await queries.upsert_guild_config(db, ...)
        """
        async with self.pool.acquire() as raw:
            yield raw
