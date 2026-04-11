"""Database table definitions (CREATE TABLE DDL) and schema migrations."""

import aiosqlite

# Current schema version. Bump this when adding a migration below.
CURRENT_SCHEMA_VERSION = 1

_BASE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS guild_config (
        guild_id        INTEGER PRIMARY KEY,
        guild_name      TEXT NOT NULL,
        scrape_channels TEXT NOT NULL DEFAULT '[]',
        excluded_users  TEXT NOT NULL DEFAULT '[]',
        default_limit   INTEGER NOT NULL DEFAULT 10000,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS scrape_jobs (
        job_id          TEXT PRIMARY KEY,
        guild_id        INTEGER NOT NULL,
        target_user_id  INTEGER,
        status          TEXT NOT NULL DEFAULT 'pending',
        channels        TEXT NOT NULL DEFAULT '[]',
        messages_found  INTEGER NOT NULL DEFAULT 0,
        messages_stored INTEGER NOT NULL DEFAULT 0,
        started_at      TEXT,
        completed_at    TEXT,
        error_message   TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (guild_id) REFERENCES guild_config(guild_id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_scrape_jobs_guild_status
        ON scrape_jobs(guild_id, status, created_at DESC);

    CREATE TABLE IF NOT EXISTS messages (
        message_id      INTEGER PRIMARY KEY,
        guild_id        INTEGER NOT NULL,
        channel_id      INTEGER NOT NULL,
        channel_name    TEXT NOT NULL DEFAULT '',
        author_id       INTEGER NOT NULL,
        author_name     TEXT NOT NULL,
        content         TEXT NOT NULL,
        timestamp       TEXT NOT NULL,
        reaction_count  INTEGER NOT NULL DEFAULT 0,
        reply_to_id     INTEGER,
        thread_id       INTEGER,
        is_pinned       INTEGER NOT NULL DEFAULT 0,
        attachment_count INTEGER NOT NULL DEFAULT 0,
        embed_count     INTEGER NOT NULL DEFAULT 0,
        word_count      INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_messages_user_time
        ON messages(guild_id, author_id, timestamp);
    CREATE INDEX IF NOT EXISTS idx_messages_channel
        ON messages(guild_id, channel_id, timestamp);
    CREATE INDEX IF NOT EXISTS idx_messages_reply
        ON messages(reply_to_id) WHERE reply_to_id IS NOT NULL;

    CREATE TABLE IF NOT EXISTS downloaded_media (
        media_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id      INTEGER NOT NULL,
        guild_id        INTEGER NOT NULL,
        original_url    TEXT NOT NULL,
        local_path      TEXT NOT NULL,
        content_type    TEXT,
        file_size       INTEGER,
        downloaded_at   TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_media_message
        ON downloaded_media(message_id);
"""


async def _get_schema_version(db: aiosqlite.Connection) -> int:
    async with db.execute("PRAGMA user_version") as cursor:
        row = await cursor.fetchone()
        return int(row[0]) if row else 0


async def create_tables(db: aiosqlite.Connection) -> None:
    """Create all database tables and apply any pending migrations.

    Uses SQLite's built-in `PRAGMA user_version` to track schema state
    rather than a try/except-on-ALTER-TABLE dance. Add future migrations
    by bumping CURRENT_SCHEMA_VERSION and adding a branch below.
    """
    await db.executescript(_BASE_SCHEMA)

    version = await _get_schema_version(db)
    # Future migrations go here, e.g.:
    # if version < 2:
    #     await db.execute("ALTER TABLE messages ADD COLUMN foo TEXT ...")

    if version != CURRENT_SCHEMA_VERSION:
        await db.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")

    await db.commit()
