"""All database query functions. No raw SQL outside this file and models.py."""

import json
from datetime import datetime, timezone

import aiosqlite
from pydantic import BaseModel, Field

from personabot.schemas.discord import TERMINAL_STATUSES, DiscordMessage, JobStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def utc_now_sqlite() -> str:
    """UTC now formatted for SQLite datetime comparison."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_to_scrape_job(row: aiosqlite.Row) -> "ScrapeJob":
    """Convert a database row to a ScrapeJob model."""
    return ScrapeJob(
        job_id=row["job_id"],
        guild_id=row["guild_id"],
        target_user_id=row["target_user_id"],
        status=row["status"],
        channels=json.loads(row["channels"]),
        messages_found=row["messages_found"],
        messages_stored=row["messages_stored"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        error_message=row["error_message"],
        created_at=row["created_at"],
    )


def _row_to_discord_message(row: aiosqlite.Row) -> DiscordMessage:
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
        is_pinned=bool(row["is_pinned"]),
        attachment_count=row["attachment_count"],
        embed_count=row["embed_count"],
        word_count=row["word_count"],
    )


_UPSERT_MESSAGE_SQL = """
    INSERT INTO messages (message_id, guild_id, channel_id, channel_name,
                          author_id, author_name, content, timestamp,
                          reaction_count, reply_to_id, thread_id, is_pinned,
                          attachment_count, embed_count, word_count)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(message_id) DO UPDATE SET
        content = excluded.content,
        reaction_count = excluded.reaction_count,
        is_pinned = excluded.is_pinned,
        attachment_count = excluded.attachment_count,
        embed_count = excluded.embed_count,
        word_count = excluded.word_count
"""


def _message_to_params(msg: DiscordMessage) -> tuple:
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
        int(msg.is_pinned),
        msg.attachment_count,
        msg.embed_count,
        msg.word_count,
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
    created_at: str = ""
    updated_at: str = ""


class ScrapeJob(BaseModel):
    """Scrape job row."""

    job_id: str
    guild_id: int
    target_user_id: int | None = None
    status: JobStatus = JobStatus.PENDING
    channels: list[int] = Field(default_factory=list)
    messages_found: int = 0
    messages_stored: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None
    created_at: str = ""


class DownloadedMedia(BaseModel):
    """Downloaded media row."""

    media_id: int
    message_id: int
    guild_id: int
    original_url: str
    local_path: str
    content_type: str | None = None
    file_size: int | None = None
    downloaded_at: str = ""


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
    db: aiosqlite.Connection,
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
    Caller is responsible for committing.
    """
    now = utc_now_sqlite()
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            guild_name = excluded.guild_name,
            scrape_channels = excluded.scrape_channels,
            included_users = excluded.included_users,
            scrape_start_date = excluded.scrape_start_date,
            scrape_end_date = excluded.scrape_end_date,
            default_limit = excluded.default_limit,
            updated_at = excluded.updated_at
        """,
        (
            guild_id,
            guild_name,
            json.dumps(resolved_channels),
            json.dumps(resolved_included),
            resolved_start,
            resolved_end,
            resolved_limit,
            now,
            now,
        ),
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


async def get_guild_config(
    db: aiosqlite.Connection, guild_id: int
) -> GuildConfig | None:
    """Fetch guild configuration."""
    async with db.execute(
        "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
    ) as cursor:
        row = await cursor.fetchone()
        if row is None:
            return None
        return GuildConfig(
            guild_id=row["guild_id"],
            guild_name=row["guild_name"],
            scrape_channels=json.loads(row["scrape_channels"]),
            included_users=json.loads(row["included_users"]),
            scrape_start_date=row["scrape_start_date"],
            scrape_end_date=row["scrape_end_date"],
            default_limit=row["default_limit"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


async def reset_guild_config(db: aiosqlite.Connection, guild_id: int) -> bool:
    """Clear all user-configurable fields on a guild config row.

    Wipes channels, included users, scrape window dates, and the message
    limit back to defaults. Preserves `guild_id`, `guild_name`, and
    `created_at` so the row retains its identity and audit timestamp.
    Returns True if a row was updated. Caller is responsible for committing.
    """
    cursor = await db.execute(
        """
        UPDATE guild_config
        SET scrape_channels   = '[]',
            included_users    = '[]',
            scrape_start_date = NULL,
            scrape_end_date   = NULL,
            default_limit     = 10000,
            updated_at        = ?
        WHERE guild_id = ?
        """,
        (utc_now_sqlite(), guild_id),
    )
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Scrape jobs
# ---------------------------------------------------------------------------


async def create_scrape_job(
    db: aiosqlite.Connection,
    job_id: str,
    guild_id: int,
    target_user_id: int | None = None,
    channels: list[int] | None = None,
) -> ScrapeJob:
    """Create a new scrape job. Caller is responsible for committing."""
    channels_json = json.dumps(channels or [])
    now = utc_now_sqlite()

    await db.execute(
        """
        INSERT INTO scrape_jobs (job_id, guild_id, target_user_id, status, channels,
                                 started_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, guild_id, target_user_id, JobStatus.RUNNING, channels_json, now, now),
    )
    return await get_scrape_job(db, job_id)  # type: ignore[return-value]


async def update_scrape_job_status(
    db: aiosqlite.Connection,
    job_id: str,
    status: JobStatus,
    messages_found: int | None = None,
    messages_stored: int | None = None,
    error_message: str | None = None,
) -> None:
    """Update scrape job status and counters. Caller is responsible for committing."""
    completed_at = utc_now_sqlite() if status in TERMINAL_STATUSES else None

    await db.execute(
        """
        UPDATE scrape_jobs
        SET status = ?,
            messages_found = COALESCE(?, messages_found),
            messages_stored = COALESCE(?, messages_stored),
            error_message = COALESCE(?, error_message),
            completed_at = COALESCE(?, completed_at)
        WHERE job_id = ?
        """,
        (status, messages_found, messages_stored, error_message, completed_at, job_id),
    )


async def get_scrape_job(db: aiosqlite.Connection, job_id: str) -> ScrapeJob | None:
    """Fetch a scrape job by ID."""
    async with db.execute(
        "SELECT * FROM scrape_jobs WHERE job_id = ?", (job_id,)
    ) as cursor:
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_scrape_job(row)


async def get_active_scrape_jobs(
    db: aiosqlite.Connection, guild_id: int
) -> list[ScrapeJob]:
    """Fetch all running scrape jobs for a guild."""
    async with db.execute(
        "SELECT * FROM scrape_jobs WHERE guild_id = ? AND status = ?",
        (guild_id, JobStatus.RUNNING),
    ) as cursor:
        rows = await cursor.fetchall()
        return [_row_to_scrape_job(row) for row in rows]


async def count_scrape_jobs(db: aiosqlite.Connection, guild_id: int) -> int:
    """Return how many scrape jobs have ever been created for a guild.

    Used by /pb export to distinguish "this guild has never scraped" from
    "this user wasn't in the scraped data" when producing empty-result errors.
    """
    async with db.execute(
        "SELECT COUNT(*) FROM scrape_jobs WHERE guild_id = ?", (guild_id,)
    ) as cursor:
        row = await cursor.fetchone()
        return int(row[0]) if row else 0


async def get_recent_scrape_jobs(
    db: aiosqlite.Connection,
    guild_id: int,
    status_filter: JobStatus | None = None,
    limit: int = 25,
) -> list[ScrapeJob]:
    """Fetch recent scrape jobs for a guild, optionally filtered by status."""
    clauses = ["guild_id = ?"]
    params: list = [guild_id]
    if status_filter is not None:
        clauses.append("status = ?")
        params.append(status_filter)
    query = (
        f"SELECT * FROM scrape_jobs WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at DESC LIMIT ?"
    )
    params.append(limit)

    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
        return [_row_to_scrape_job(row) for row in rows]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def upsert_message(db: aiosqlite.Connection, msg: DiscordMessage) -> None:
    """Insert or update a single message. Caller is responsible for committing."""
    await db.execute(_UPSERT_MESSAGE_SQL, _message_to_params(msg))


async def upsert_messages_batch(
    db: aiosqlite.Connection, messages: list[DiscordMessage]
) -> int:
    """Batch upsert messages. Returns count submitted. Caller commits."""
    await db.executemany(
        _UPSERT_MESSAGE_SQL,
        [_message_to_params(m) for m in messages],
    )
    return len(messages)


async def get_user_messages(
    db: aiosqlite.Connection,
    guild_id: int,
    author_id: int,
    limit: int | None = None,
) -> list[DiscordMessage]:
    """Fetch all messages for a user in a guild, ordered by timestamp."""
    query = """
        SELECT * FROM messages
        WHERE guild_id = ? AND author_id = ?
        ORDER BY timestamp ASC
    """
    params: list[int] = [guild_id, author_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
        return [_row_to_discord_message(row) for row in rows]


async def get_message_by_id(
    db: aiosqlite.Connection, message_id: int
) -> DiscordMessage | None:
    """Fetch a single message by ID."""
    async with db.execute(
        "SELECT * FROM messages WHERE message_id = ?", (message_id,)
    ) as cursor:
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_discord_message(row)


async def get_messages_by_ids(
    db: aiosqlite.Connection, message_ids: set[int]
) -> dict[int, DiscordMessage]:
    """Fetch multiple messages by ID for reply context lookup."""
    if not message_ids:
        return {}
    placeholders = ",".join("?" * len(message_ids))
    query = f"SELECT * FROM messages WHERE message_id IN ({placeholders})"
    async with db.execute(query, tuple(message_ids)) as cursor:
        rows = await cursor.fetchall()
        return {row["message_id"]: _row_to_discord_message(row) for row in rows}


def _row_to_downloaded_media(row: aiosqlite.Row) -> "DownloadedMedia":
    """Convert a database row to a DownloadedMedia model."""
    return DownloadedMedia(
        media_id=row["media_id"],
        message_id=row["message_id"],
        guild_id=row["guild_id"],
        original_url=row["original_url"],
        local_path=row["local_path"],
        content_type=row["content_type"],
        file_size=row["file_size"],
        downloaded_at=row["downloaded_at"],
    )


# ---------------------------------------------------------------------------
# Downloaded media
# ---------------------------------------------------------------------------


async def save_downloaded_media(
    db: aiosqlite.Connection,
    message_id: int,
    guild_id: int,
    original_url: str,
    local_path: str,
    content_type: str | None = None,
    file_size: int | None = None,
) -> DownloadedMedia:
    """Save a downloaded media record. Caller is responsible for committing."""
    cursor = await db.execute(
        """
        INSERT INTO downloaded_media (message_id, guild_id, original_url,
                                      local_path, content_type, file_size)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (message_id, guild_id, original_url, local_path, content_type, file_size),
    )
    media_id = cursor.lastrowid
    if media_id is None:
        raise RuntimeError("Failed to insert media record")
    return DownloadedMedia(
        media_id=media_id,
        message_id=message_id,
        guild_id=guild_id,
        original_url=original_url,
        local_path=local_path,
        content_type=content_type,
        file_size=file_size,
    )


async def get_media_for_user(
    db: aiosqlite.Connection, guild_id: int, author_id: int
) -> list[DownloadedMedia]:
    """Get all downloaded media for a user's messages."""
    async with db.execute(
        """
        SELECT dm.* FROM downloaded_media dm
        JOIN messages m ON dm.message_id = m.message_id
        WHERE dm.guild_id = ? AND m.author_id = ?
        ORDER BY dm.downloaded_at ASC
        """,
        (guild_id, author_id),
    ) as cursor:
        rows = await cursor.fetchall()
        return [_row_to_downloaded_media(row) for row in rows]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


async def get_user_stats(
    db: aiosqlite.Connection, guild_id: int, author_id: int
) -> UserStats | None:
    """Get aggregated message stats for a user."""
    async with db.execute(
        """
        SELECT author_id, author_name,
               COUNT(*) as message_count,
               AVG(word_count) as avg_word_count,
               SUM(reaction_count) as total_reactions,
               MIN(timestamp) as first_message,
               MAX(timestamp) as last_message
        FROM messages
        WHERE guild_id = ? AND author_id = ?
        GROUP BY author_id
        """,
        (guild_id, author_id),
    ) as cursor:
        row = await cursor.fetchone()
        if row is None:
            return None
        return UserStats(
            author_id=row["author_id"],
            author_name=row["author_name"],
            message_count=row["message_count"],
            avg_word_count=row["avg_word_count"] or 0.0,
            total_reactions=row["total_reactions"] or 0,
            first_message=row["first_message"],
            last_message=row["last_message"],
        )


async def get_server_stats(db: aiosqlite.Connection, guild_id: int) -> ServerStats:
    """Get aggregated stats for the entire server."""
    async with db.execute(
        """
        SELECT COUNT(*) as total_messages,
               COUNT(DISTINCT author_id) as unique_users,
               COUNT(DISTINCT channel_id) as total_channels,
               COALESCE(SUM(reaction_count), 0) as total_reactions,
               (SELECT COUNT(*) FROM scrape_jobs WHERE guild_id = ?) as total_scrape_jobs
        FROM messages
        WHERE guild_id = ?
        """,
        (guild_id, guild_id),
    ) as cursor:
        row = await cursor.fetchone()
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
    db: aiosqlite.Connection, guild_id: int, n: int = 10
) -> list[TopUser]:
    """Get top N users by message count for a guild."""
    async with db.execute(
        """
        SELECT author_id, author_name,
               COUNT(*) as message_count,
               COALESCE(SUM(reaction_count), 0) as total_reactions
        FROM messages
        WHERE guild_id = ?
        GROUP BY author_id
        ORDER BY message_count DESC
        LIMIT ?
        """,
        (guild_id, n),
    ) as cursor:
        rows = await cursor.fetchall()
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


async def delete_expired_media(db: aiosqlite.Connection, cutoff: str) -> list[str]:
    """Delete downloaded_media rows older than cutoff.

    Returns local_paths for filesystem cleanup.
    Must be called BEFORE delete_expired_messages (FK constraint).
    Caller is responsible for committing.
    """
    async with db.execute(
        "SELECT local_path FROM downloaded_media WHERE downloaded_at < ?",
        (cutoff,),
    ) as cursor:
        rows = await cursor.fetchall()
        paths = [row["local_path"] for row in rows]

    await db.execute("DELETE FROM downloaded_media WHERE downloaded_at < ?", (cutoff,))
    return paths


async def delete_expired_messages(db: aiosqlite.Connection, cutoff: str) -> int:
    """Delete messages older than cutoff. Returns count deleted. Caller commits."""
    cursor = await db.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
    return cursor.rowcount


async def delete_expired_scrape_jobs(db: aiosqlite.Connection, cutoff: str) -> int:
    """Delete completed/failed/cancelled scrape jobs older than cutoff.

    Running/pending jobs are never deleted -- only finished or stale jobs.
    Caller is responsible for committing.
    """
    cursor = await db.execute(
        """
        DELETE FROM scrape_jobs
        WHERE status NOT IN (?, ?)
          AND (
              (completed_at IS NOT NULL AND completed_at < ?)
              OR (completed_at IS NULL AND created_at < ?)
          )
        """,
        (JobStatus.RUNNING, JobStatus.PENDING, cutoff, cutoff),
    )
    return cursor.rowcount
