"""All database query functions. No raw SQL outside this file and models.py.

Every function takes an ``asyncpg.Connection`` as its first argument.
Callers acquire one from ``bot.db_conn()`` or the test fixture and wrap
writes in an ``async with db.transaction():`` block. Queries here never
manage commit boundaries.

Placeholder style is Postgres ``$N`` positional. JSONB columns
(``scrape_channels``, ``included_users``, ``channels``) use the asyncpg
JSONB codec registered in ``db/manager._register_codecs`` so Python
``list[int]`` round-trips natively without json.dumps/loads.
"""

from datetime import datetime, timezone
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from personabot.schemas.discord import TERMINAL_STATUSES, DiscordMessage, JobStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rowcount(status: str) -> int:
    """Parse asyncpg execute() command tag for row count.

    asyncpg returns strings like ``"DELETE 5"`` or ``"UPDATE 0"`` from
    ``execute()``. The row count is always the last whitespace-separated
    token. For ``INSERT`` the tag is ``"INSERT 0 N"`` (the first zero is
    the legacy OID field), so the last token is still the count.
    """
    return int(status.split()[-1])


def _row_to_scrape_job(row: asyncpg.Record) -> "ScrapeJob":
    """Convert a database row to a ScrapeJob model.

    ``started_at`` and ``created_at`` stay in SQL (``created_at`` is
    used in the recent-jobs ORDER BY) but aren't exposed on the Python
    model — no consumer reads them.
    """
    return ScrapeJob(
        job_id=row["job_id"],
        guild_id=row["guild_id"],
        status=row["status"],
        channels=row["channels"],
        messages_found=row["messages_found"],
        messages_stored=row["messages_stored"],
        completed_at=row["completed_at"],
        error_message=row["error_message"],
    )


def _row_to_discord_message(row: asyncpg.Record) -> DiscordMessage:
    """Convert a database row to a DiscordMessage model.

    Uses model_construct to skip re-validation — DB data is trusted (it was
    validated on insert). Hot path for exports that fetch 10k+ messages.
    """
    return DiscordMessage.model_construct(
        message_id=row["message_id"],
        guild_id=row["guild_id"],
        channel_id=row["channel_id"],
        channel_name=row["channel_name"],
        author_id=row["author_id"],
        author_name=row["author_name"],
        content=row["content"],
        timestamp=row["timestamp"],
        reaction_count=row["reaction_count"],
        reply_to_id=row["reply_to_id"],
        thread_id=row["thread_id"],
        is_pinned=row["is_pinned"],
        attachment_count=row["attachment_count"],
        embed_count=row["embed_count"],
        word_count=row["word_count"],
    )


# First-wins invariant: `job_id` must NEVER appear in the ON CONFLICT
# UPDATE SET list below. The scrape that first captured a message owns
# it forever so `/pb export generate job_id:X` returns a stable corpus
# even when scrape X+1 re-hits the same rows. The module-level assertion
# after the SQL locks this in at import time — adding
# `job_id = EXCLUDED.job_id` to the SET clause will break the bot boot.
_UPSERT_MESSAGE_SQL = """
    INSERT INTO messages (message_id, guild_id, channel_id, channel_name,
                          author_id, author_name, content, timestamp,
                          reaction_count, reply_to_id, thread_id, is_pinned,
                          attachment_count, embed_count, word_count, job_id)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
    ON CONFLICT (message_id) DO UPDATE SET
        content          = EXCLUDED.content,
        reaction_count   = EXCLUDED.reaction_count,
        is_pinned        = EXCLUDED.is_pinned,
        attachment_count = EXCLUDED.attachment_count,
        embed_count      = EXCLUDED.embed_count,
        word_count       = EXCLUDED.word_count
"""

# Structural guard: if a future edit accidentally adds `job_id` to the
# SET clause, the bot fails to import. Cheaper than waiting for a test.
assert "job_id" not in _UPSERT_MESSAGE_SQL.split("DO UPDATE SET", 1)[1], (
    "First-wins invariant violated: job_id must not appear in "
    "_UPSERT_MESSAGE_SQL's ON CONFLICT UPDATE SET clause."
)


def _message_to_params(msg: DiscordMessage, *, job_id: str | None) -> tuple[Any, ...]:
    """Extract a parameter tuple from a DiscordMessage for the upsert SQL."""
    return (
        msg.message_id,
        msg.guild_id,
        msg.channel_id,
        msg.channel_name,
        msg.author_id,
        msg.author_name,
        msg.content,
        msg.timestamp,
        msg.reaction_count,
        msg.reply_to_id,
        msg.thread_id,
        msg.is_pinned,
        msg.attachment_count,
        msg.embed_count,
        msg.word_count,
        job_id,
    )


# ---------------------------------------------------------------------------
# Row models (small models for DB rows that aren't full DiscordMessages)
# ---------------------------------------------------------------------------


class GuildConfig(BaseModel):
    """Guild configuration row."""

    guild_id: int
    guild_name: str
    scrape_channels: list[int] = Field(default_factory=list)
    included_users: list[int] = Field(default_factory=list)
    scrape_start_date: str | None = None  # YYYY-MM-DD (UTC)
    scrape_end_date: str | None = None  # YYYY-MM-DD (UTC)
    default_limit: int = 10000
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ScrapeJob(BaseModel):
    """Scrape job row."""

    job_id: str
    guild_id: int
    status: JobStatus = JobStatus.PENDING
    channels: list[int] = Field(default_factory=list)
    messages_found: int = 0
    messages_stored: int = 0
    completed_at: datetime | None = None
    error_message: str | None = None


class UserStats(BaseModel):
    """Aggregated stats for a single user."""

    author_id: int
    author_name: str
    message_count: int
    avg_word_count: float
    total_reactions: int
    first_message: str
    last_message: str


class ServerStats(BaseModel):
    """Aggregated stats for the entire server."""

    total_messages: int
    unique_users: int
    total_channels: int
    total_reactions: int
    total_scrape_jobs: int


# ---------------------------------------------------------------------------
# Guild config
# ---------------------------------------------------------------------------


async def upsert_guild_config(
    db: asyncpg.Connection,
    guild_id: int,
    guild_name: str,
    scrape_channels: list[int] | None = None,
    included_users: list[int] | None = None,
    scrape_start_date: str | None = None,
    scrape_end_date: str | None = None,
    default_limit: int | None = None,
) -> GuildConfig:
    """Insert or update guild configuration.

    None values mean "keep existing" — only non-None fields are updated.
    For new guilds, None falls back to schema defaults ([]/NULL/10000).
    To explicitly clear fields to defaults, call `reset_guild_config`.
    Caller is responsible for wrapping writes in ``db.transaction()``.
    """
    now = datetime.now(timezone.utc)
    existing = await get_guild_config(db, guild_id)

    # Merge: provided value wins, else keep existing, else schema default.
    resolved_channels: list[int] = (
        scrape_channels
        if scrape_channels is not None
        else (existing.scrape_channels if existing else [])
    )
    resolved_included: list[int] = (
        included_users
        if included_users is not None
        else (existing.included_users if existing else [])
    )
    resolved_start = (
        scrape_start_date
        if scrape_start_date is not None
        else (existing.scrape_start_date if existing else None)
    )
    resolved_end = (
        scrape_end_date
        if scrape_end_date is not None
        else (existing.scrape_end_date if existing else None)
    )
    resolved_limit = (
        default_limit
        if default_limit is not None
        else (existing.default_limit if existing else 10000)
    )

    await db.execute(
        """
        INSERT INTO guild_config (guild_id, guild_name, scrape_channels,
                                  included_users, scrape_start_date,
                                  scrape_end_date, default_limit,
                                  created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
        ON CONFLICT (guild_id) DO UPDATE SET
            guild_name        = EXCLUDED.guild_name,
            scrape_channels   = EXCLUDED.scrape_channels,
            included_users    = EXCLUDED.included_users,
            scrape_start_date = EXCLUDED.scrape_start_date,
            scrape_end_date   = EXCLUDED.scrape_end_date,
            default_limit     = EXCLUDED.default_limit,
            updated_at        = EXCLUDED.updated_at
        """,
        guild_id,
        guild_name,
        resolved_channels,
        resolved_included,
        resolved_start,
        resolved_end,
        resolved_limit,
        now,
    )
    # Construct the return locally from merged values rather than re-SELECTing.
    # created_at is preserved on update; for fresh inserts it defaults to `now`.
    return GuildConfig(
        guild_id=guild_id,
        guild_name=guild_name,
        scrape_channels=resolved_channels,
        included_users=resolved_included,
        scrape_start_date=resolved_start,
        scrape_end_date=resolved_end,
        default_limit=resolved_limit,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )


async def get_guild_config(db: asyncpg.Connection, guild_id: int) -> GuildConfig | None:
    """Fetch guild configuration."""
    row = await db.fetchrow(
        "SELECT * FROM guild_config WHERE guild_id = $1",
        guild_id,
    )
    if row is None:
        return None
    return GuildConfig(
        guild_id=row["guild_id"],
        guild_name=row["guild_name"],
        scrape_channels=row["scrape_channels"],
        included_users=row["included_users"],
        scrape_start_date=row["scrape_start_date"],
        scrape_end_date=row["scrape_end_date"],
        default_limit=row["default_limit"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def reset_guild_config(db: asyncpg.Connection, guild_id: int) -> bool:
    """Clear all user-configurable fields on a guild config row.

    Wipes channels, included users, scrape window dates, and the message
    limit back to defaults. Preserves `guild_id`, `guild_name`, and
    `created_at` so the row retains its identity and audit timestamp.
    Returns True if a row was updated. Caller wraps writes in a transaction.
    """
    status = await db.execute(
        """
        UPDATE guild_config
        SET scrape_channels   = '[]'::jsonb,
            included_users    = '[]'::jsonb,
            scrape_start_date = NULL,
            scrape_end_date   = NULL,
            default_limit     = 10000,
            updated_at        = $1
        WHERE guild_id = $2
        """,
        datetime.now(timezone.utc),
        guild_id,
    )
    return _rowcount(status) > 0


# ---------------------------------------------------------------------------
# Scrape jobs
# ---------------------------------------------------------------------------


async def create_scrape_job(
    db: asyncpg.Connection,
    job_id: str,
    guild_id: int,
    channels: list[int] | None = None,
) -> ScrapeJob:
    """Create a new scrape job. Caller wraps writes in a transaction."""
    now = datetime.now(timezone.utc)

    await db.execute(
        """
        INSERT INTO scrape_jobs (job_id, guild_id, status, channels,
                                 started_at, created_at)
        VALUES ($1, $2, $3, $4, $5, $5)
        """,
        job_id,
        guild_id,
        JobStatus.RUNNING,
        channels or [],
        now,
    )
    return await get_scrape_job(db, job_id)  # type: ignore[return-value]


async def update_scrape_job_status(
    db: asyncpg.Connection,
    job_id: str,
    status: JobStatus,
    messages_found: int | None = None,
    messages_stored: int | None = None,
    error_message: str | None = None,
) -> None:
    """Update scrape job status and counters. Caller wraps in a transaction."""
    completed_at = datetime.now(timezone.utc) if status in TERMINAL_STATUSES else None

    await db.execute(
        """
        UPDATE scrape_jobs
        SET status          = $1,
            messages_found  = COALESCE($2, messages_found),
            messages_stored = COALESCE($3, messages_stored),
            error_message   = COALESCE($4, error_message),
            completed_at    = COALESCE($5, completed_at)
        WHERE job_id = $6
        """,
        status,
        messages_found,
        messages_stored,
        error_message,
        completed_at,
        job_id,
    )


async def get_scrape_job(db: asyncpg.Connection, job_id: str) -> ScrapeJob | None:
    """Fetch a scrape job by ID."""
    row = await db.fetchrow(
        "SELECT * FROM scrape_jobs WHERE job_id = $1",
        job_id,
    )
    if row is None:
        return None
    return _row_to_scrape_job(row)


async def count_scrape_jobs(db: asyncpg.Connection, guild_id: int) -> int:
    """Return how many scrape jobs have ever been created for a guild.

    Used by /pb export to distinguish "this guild has never scraped" from
    "this user wasn't in the scraped data" when producing empty-result errors.
    """
    val = await db.fetchval(
        "SELECT COUNT(*) FROM scrape_jobs WHERE guild_id = $1",
        guild_id,
    )
    return int(val or 0)


async def get_recent_scrape_jobs(
    db: asyncpg.Connection,
    guild_id: int,
    status_filter: JobStatus | None = None,
    limit: int = 25,
) -> list[ScrapeJob]:
    """Fetch recent scrape jobs for a guild, optionally filtered by status.

    Unlike SQLite's ``?`` placeholders, Postgres ``$N`` positions are fixed
    by the query string — we renumber manually as the WHERE clause grows.
    """
    if status_filter is not None:
        query = (
            "SELECT * FROM scrape_jobs "
            "WHERE guild_id = $1 AND status = $2 "
            "ORDER BY created_at DESC LIMIT $3"
        )
        rows = await db.fetch(query, guild_id, status_filter, limit)
    else:
        query = (
            "SELECT * FROM scrape_jobs "
            "WHERE guild_id = $1 "
            "ORDER BY created_at DESC LIMIT $2"
        )
        rows = await db.fetch(query, guild_id, limit)
    return [_row_to_scrape_job(row) for row in rows]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def upsert_message(
    db: asyncpg.Connection,
    msg: DiscordMessage,
    *,
    job_id: str | None = None,
) -> None:
    """Insert or update a single message. Caller wraps in a transaction.

    ``job_id`` stamps the message with the scrape that first captured it
    (see first-wins note on ``_UPSERT_MESSAGE_SQL``). Tests that don't
    care about scrape provenance pass ``None``.
    """
    await db.execute(_UPSERT_MESSAGE_SQL, *_message_to_params(msg, job_id=job_id))


async def upsert_messages_batch(
    db: asyncpg.Connection,
    messages: list[DiscordMessage],
    *,
    job_id: str | None = None,
) -> int:
    """Batch upsert messages. Returns count submitted.

    Uses ``executemany`` which asyncpg supports with ``ON CONFLICT`` as
    long as the statement has no ``RETURNING`` clause (ours does not).
    """
    await db.executemany(
        _UPSERT_MESSAGE_SQL,
        [_message_to_params(m, job_id=job_id) for m in messages],
    )
    return len(messages)


async def get_user_messages(
    db: asyncpg.Connection,
    guild_id: int,
    author_id: int,
    *,
    job_id: str | None = None,
    limit: int | None = None,
) -> list[DiscordMessage]:
    """Fetch all messages for a user in a guild, ordered by timestamp.

    When ``job_id`` is provided, filters to messages that scrape first
    captured (see first-wins note on ``_UPSERT_MESSAGE_SQL``).
    """
    clauses = ["guild_id = $1", "author_id = $2"]
    params: list[Any] = [guild_id, author_id]
    if job_id is not None:
        clauses.append(f"job_id = ${len(params) + 1}")
        params.append(job_id)
    query = (
        f"SELECT * FROM messages WHERE {' AND '.join(clauses)} "
        "ORDER BY timestamp ASC"
    )
    if limit is not None:
        query += f" LIMIT ${len(params) + 1}"
        params.append(limit)
    rows = await db.fetch(query, *params)
    return [_row_to_discord_message(row) for row in rows]


async def get_message_by_id(
    db: asyncpg.Connection, message_id: int
) -> DiscordMessage | None:
    """Fetch a single message by ID."""
    row = await db.fetchrow(
        "SELECT * FROM messages WHERE message_id = $1",
        message_id,
    )
    if row is None:
        return None
    return _row_to_discord_message(row)


async def get_messages_by_ids(
    db: asyncpg.Connection, message_ids: set[int]
) -> dict[int, DiscordMessage]:
    """Fetch multiple messages by ID for reply context lookup.

    Uses ``= ANY($1::bigint[])`` rather than an expanded IN list so the
    query plan is stable regardless of set size and we avoid dynamic
    placeholder arithmetic.
    """
    if not message_ids:
        return {}
    rows = await db.fetch(
        "SELECT * FROM messages WHERE message_id = ANY($1::bigint[])",
        list(message_ids),
    )
    return {row["message_id"]: _row_to_discord_message(row) for row in rows}


# ---------------------------------------------------------------------------
# Downloaded media
# ---------------------------------------------------------------------------


async def save_downloaded_media(
    db: asyncpg.Connection,
    message_id: int,
    guild_id: int,
    original_url: str,
    local_path: str,
    content_type: str | None = None,
    file_size: int | None = None,
) -> None:
    """Save a downloaded media record. Caller wraps in a transaction."""
    await db.execute(
        """
        INSERT INTO downloaded_media (message_id, guild_id, original_url,
                                      local_path, content_type, file_size)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        message_id,
        guild_id,
        original_url,
        local_path,
        content_type,
        file_size,
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


async def get_user_stats(
    db: asyncpg.Connection, guild_id: int, author_id: int
) -> UserStats | None:
    """Get aggregated message stats for a user."""
    row = await db.fetchrow(
        """
        SELECT author_id, author_name,
               COUNT(*) as message_count,
               AVG(word_count) as avg_word_count,
               SUM(reaction_count) as total_reactions,
               MIN(timestamp) as first_message,
               MAX(timestamp) as last_message
        FROM messages
        WHERE guild_id = $1 AND author_id = $2
        GROUP BY author_id, author_name
        """,
        guild_id,
        author_id,
    )
    if row is None:
        return None
    return UserStats(
        author_id=row["author_id"],
        author_name=row["author_name"],
        message_count=row["message_count"],
        avg_word_count=float(row["avg_word_count"] or 0.0),
        total_reactions=row["total_reactions"] or 0,
        first_message=row["first_message"],
        last_message=row["last_message"],
    )


async def get_server_stats(db: asyncpg.Connection, guild_id: int) -> ServerStats:
    """Get aggregated stats for the entire server."""
    row = await db.fetchrow(
        """
        SELECT COUNT(*) as total_messages,
               COUNT(DISTINCT author_id) as unique_users,
               COUNT(DISTINCT channel_id) as total_channels,
               COALESCE(SUM(reaction_count), 0) as total_reactions,
               (SELECT COUNT(*) FROM scrape_jobs WHERE guild_id = $1) as total_scrape_jobs
        FROM messages
        WHERE guild_id = $1
        """,
        guild_id,
    )
    if row is None:
        raise RuntimeError("COUNT query returned no rows")

    return ServerStats(
        total_messages=row["total_messages"],
        unique_users=row["unique_users"],
        total_channels=row["total_channels"],
        total_reactions=row["total_reactions"],
        total_scrape_jobs=row["total_scrape_jobs"],
    )


class TopUser(BaseModel):
    """A user ranked by message count."""

    author_id: int
    author_name: str
    message_count: int
    total_reactions: int


async def get_top_users(
    db: asyncpg.Connection,
    guild_id: int,
    n: int = 10,
    *,
    job_id: str | None = None,
) -> list[TopUser]:
    """Get top N users by message count for a guild.

    When ``job_id`` is provided, scopes the count/sum to messages that
    scrape first captured.
    """
    clauses = ["guild_id = $1"]
    params: list[Any] = [guild_id]
    if job_id is not None:
        clauses.append(f"job_id = ${len(params) + 1}")
        params.append(job_id)
    query = f"""
        SELECT author_id, author_name,
               COUNT(*) as message_count,
               COALESCE(SUM(reaction_count), 0) as total_reactions
        FROM messages
        WHERE {' AND '.join(clauses)}
        GROUP BY author_id, author_name
        ORDER BY message_count DESC
        LIMIT ${len(params) + 1}
    """
    params.append(n)
    rows = await db.fetch(query, *params)
    return [
        TopUser(
            author_id=row["author_id"],
            author_name=row["author_name"],
            message_count=row["message_count"],
            total_reactions=row["total_reactions"],
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------


async def delete_expired_media(db: asyncpg.Connection, cutoff: datetime) -> list[str]:
    """Delete downloaded_media rows older than cutoff, returning their paths.

    The DELETE ... RETURNING pattern is one round trip instead of
    SELECT + DELETE and guarantees the returned paths match exactly what
    was deleted. Must be called BEFORE delete_expired_messages (FK).
    Caller wraps writes in a transaction.
    """
    rows = await db.fetch(
        "DELETE FROM downloaded_media WHERE downloaded_at < $1 " "RETURNING local_path",
        cutoff,
    )
    return [row["local_path"] for row in rows]


async def delete_expired_messages(db: asyncpg.Connection, cutoff: datetime) -> int:
    """Delete messages older than cutoff. Returns count deleted. Caller commits."""
    status = await db.execute(
        "DELETE FROM messages WHERE created_at < $1",
        cutoff,
    )
    return _rowcount(status)


async def delete_expired_scrape_jobs(db: asyncpg.Connection, cutoff: datetime) -> int:
    """Delete completed/failed/cancelled scrape jobs older than cutoff.

    Running/pending jobs are never deleted -- only finished or stale jobs.
    Caller wraps writes in a transaction.
    """
    status = await db.execute(
        """
        DELETE FROM scrape_jobs
        WHERE status NOT IN ($1, $2)
          AND (
              (completed_at IS NOT NULL AND completed_at < $3)
              OR (completed_at IS NULL AND created_at < $3)
          )
        """,
        JobStatus.RUNNING,
        JobStatus.PENDING,
        cutoff,
    )
    return _rowcount(status)


# ---------------------------------------------------------------------------
# Manual guild data reset (/pb db reset)
# ---------------------------------------------------------------------------


async def reset_guild_data(
    db: asyncpg.Connection, guild_id: int
) -> tuple[list[str], int, int]:
    """Delete all scraped data for a guild. Does NOT touch ``guild_config``.

    Returns ``(media_paths, message_count, scrape_job_count)``. The
    caller wraps this in ``db.transaction()`` and performs the
    filesystem sweep outside the transaction (after the pool
    connection is released).

    Delete ordering is load-bearing:
      1. ``downloaded_media`` with ``RETURNING local_path`` — captures
         the on-disk paths before the rows disappear. A messages-first
         delete would cascade these rows via the FK and lose the paths.
      2. ``messages`` — the cascade from step 1 is a no-op now.
      3. ``scrape_jobs`` — standalone, no ordering constraint.
    """
    media_rows = await db.fetch(
        "DELETE FROM downloaded_media WHERE guild_id = $1 RETURNING local_path",
        guild_id,
    )
    msg_status = await db.execute(
        "DELETE FROM messages WHERE guild_id = $1",
        guild_id,
    )
    job_status = await db.execute(
        "DELETE FROM scrape_jobs WHERE guild_id = $1",
        guild_id,
    )
    return (
        [row["local_path"] for row in media_rows],
        _rowcount(msg_status),
        _rowcount(job_status),
    )
