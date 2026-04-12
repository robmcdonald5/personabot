"""Database table definitions (CREATE TABLE DDL).

HEAVY-DEVELOPMENT MODE — NO SCHEMA VERSIONING
==============================================
Every bot startup drops and recreates all tables from scratch. All
prior guild_config, scrape_jobs, messages, and downloaded_media rows
are wiped on restart. This is intentional while the schema is
churning — writing and testing a migration for every column change
during early iteration is overhead that does not pay back yet.

When the schema stabilizes and the bot approaches production use,
reintroduce proper versioning:

  1. Add `CURRENT_SCHEMA_VERSION = N` and a `_get_schema_version`
     helper reading `PRAGMA user_version`.
  2. Split `_SCHEMA` below into a `_BASE_SCHEMA` (CREATE statements
     only, with `IF NOT EXISTS`) and strip the DROP block entirely.
     Run the base schema unconditionally so fresh installs get it
     and already-stamped installs treat it as a no-op.
  3. Add `if version < N:` ALTER branches inside an explicit
     `BEGIN IMMEDIATE ... COMMIT` with rollback-on-exception for
     crash-safe migrations. Python's sqlite3 legacy isolation mode
     does NOT auto-begin transactions for DDL, so the explicit
     BEGIN is load-bearing.
  4. Narrow the version bump guard with `<` (not `!=`) so a
     newer-than-expected DB from a code rollback is left alone.
"""

import aiosqlite

# DROP order is child-first: with `PRAGMA foreign_keys = ON` (set in
# DatabaseManager.connect) dropping a parent before its children
# raises FOREIGN KEY constraint failed when rows exist.
# downloaded_media → messages, scrape_jobs → guild_config.
_SCHEMA = """
    DROP TABLE IF EXISTS downloaded_media;
    DROP TABLE IF EXISTS scrape_jobs;
    DROP TABLE IF EXISTS messages;
    DROP TABLE IF EXISTS guild_config;

    CREATE TABLE guild_config (
        guild_id          INTEGER PRIMARY KEY,
        guild_name        TEXT NOT NULL,
        scrape_channels   TEXT NOT NULL DEFAULT '[]',
        included_users    TEXT NOT NULL DEFAULT '[]',
        scrape_start_date TEXT,
        scrape_end_date   TEXT,
        default_limit     INTEGER NOT NULL DEFAULT 10000,
        created_at        TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE scrape_jobs (
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

    CREATE INDEX idx_scrape_jobs_guild_status
        ON scrape_jobs(guild_id, status, created_at DESC);

    CREATE TABLE messages (
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

    CREATE INDEX idx_messages_user_time
        ON messages(guild_id, author_id, timestamp);
    CREATE INDEX idx_messages_channel
        ON messages(guild_id, channel_id, timestamp);
    CREATE INDEX idx_messages_reply
        ON messages(reply_to_id) WHERE reply_to_id IS NOT NULL;

    CREATE TABLE downloaded_media (
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

    CREATE INDEX idx_media_message
        ON downloaded_media(message_id);
"""


async def create_tables(db: aiosqlite.Connection) -> None:
    """Drop and recreate all tables (heavy-dev mode; see module docstring)."""
    await db.executescript(_SCHEMA)
