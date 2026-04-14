"""Database table definitions (CREATE TABLE DDL) for Postgres.

Dev mode drops and recreates every table on startup so schema churn
doesn't require a migration per change. Production schema is managed
exclusively by ``db/migrations/*.sql`` via dbmate (see the ``migrate``
init container in ``docker-compose.prod.yml``); the DROP path never
fires there. When the schema stabilizes, stop passing ``drop_first=True``
from ``DatabaseManager`` and delete ``_DROP_SQL``.

Discord ID columns are ``BIGINT`` — Postgres ``INTEGER`` is 32-bit and
would silently truncate 17–19-digit Discord snowflakes. See
``tests/db/test_bigint_ids.py`` for the regression guard.
"""

import asyncpg

# DROP order is child-first: foreign keys on scrape_jobs and downloaded_media
# reference parent rows, so dropping a parent before its children raises
# ``cannot drop table ... because other objects depend on it`` with the
# default RESTRICT behavior. We explicitly order the DROPs here rather than
# rely on CASCADE so a typo in the child ordering surfaces loudly.
_DROP_SQL = """
DROP TABLE IF EXISTS downloaded_media;
DROP TABLE IF EXISTS scrape_jobs;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS guild_config;
"""

# ``messages.guild_id`` is intentionally NOT a foreign key. Scraped
# messages must outlive guild_config churn (e.g. ``reset_guild_config``);
# retention deletes messages independently on a time axis, and a
# cascade-from-config would make that deletion path silently depend on
# the FK. Other tables DO have FKs because their semantics are tied to
# a parent row.
_CREATE_SQL = """
CREATE TABLE guild_config (
    guild_id          BIGINT PRIMARY KEY,
    guild_name        TEXT NOT NULL,
    scrape_channels   JSONB NOT NULL DEFAULT '[]'::jsonb,
    included_users    JSONB NOT NULL DEFAULT '[]'::jsonb,
    scrape_start_date TEXT,
    scrape_end_date   TEXT,
    default_limit     INTEGER NOT NULL DEFAULT 10000,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE scrape_jobs (
    job_id          TEXT PRIMARY KEY,
    guild_id        BIGINT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    channels        JSONB NOT NULL DEFAULT '[]'::jsonb,
    messages_found  INTEGER NOT NULL DEFAULT 0,
    messages_stored INTEGER NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (guild_id) REFERENCES guild_config(guild_id) ON DELETE CASCADE
);

CREATE INDEX idx_scrape_jobs_guild_status
    ON scrape_jobs(guild_id, status, created_at DESC);

CREATE TABLE messages (
    message_id       BIGINT PRIMARY KEY,
    guild_id         BIGINT NOT NULL,
    channel_id       BIGINT NOT NULL,
    channel_name     TEXT NOT NULL DEFAULT '',
    author_id        BIGINT NOT NULL,
    author_name      TEXT NOT NULL,
    content          TEXT NOT NULL,
    timestamp        TEXT NOT NULL,
    reaction_count   INTEGER NOT NULL DEFAULT 0,
    reply_to_id      BIGINT,
    thread_id        BIGINT,
    is_pinned        BOOLEAN NOT NULL DEFAULT FALSE,
    attachment_count INTEGER NOT NULL DEFAULT 0,
    embed_count      INTEGER NOT NULL DEFAULT 0,
    word_count       INTEGER NOT NULL DEFAULT 0,
    -- job_id is the scrape that FIRST captured this message. On a
    -- subsequent re-scrape the ON CONFLICT UPDATE clause in
    -- queries._UPSERT_MESSAGE_SQL leaves job_id alone (first-wins) so
    -- /pb export generate job_id:X always returns the corpus that
    -- scrape X originally discovered. No FK: scrape_jobs and messages
    -- are retained on the same wall-clock, orphan job_id strings are
    -- harmless (lookups return empty).
    job_id           TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_messages_user_time
    ON messages(guild_id, author_id, timestamp);
CREATE INDEX idx_messages_channel
    ON messages(guild_id, channel_id, timestamp);
CREATE INDEX idx_messages_reply
    ON messages(reply_to_id) WHERE reply_to_id IS NOT NULL;
CREATE INDEX idx_messages_job
    ON messages(guild_id, job_id) WHERE job_id IS NOT NULL;

CREATE TABLE downloaded_media (
    media_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id      BIGINT NOT NULL,
    guild_id        BIGINT NOT NULL,
    original_url    TEXT NOT NULL,
    local_path      TEXT NOT NULL,
    content_type    TEXT,
    file_size       BIGINT,
    downloaded_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE
);

CREATE INDEX idx_media_message
    ON downloaded_media(message_id);
"""


async def create_tables(db: asyncpg.Connection, *, drop_first: bool = False) -> None:
    """Create all tables. Drops existing tables first iff ``drop_first``.

    Callers in production must pass ``drop_first=False`` and should not
    call this at all once dbmate is in place — schema there is managed by
    migrations. Dev callers pass ``drop_first=True`` to rebuild from
    scratch on every startup.
    """
    if drop_first:
        await db.execute(_DROP_SQL)
    await db.execute(_CREATE_SQL)
