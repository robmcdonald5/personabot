-- migrate:up

-- Initial Postgres schema for PersonaBot, frozen from `src/personabot/db/models.py`
-- at the end of the SQLite → Postgres migration (Phase 2).
--
-- In production this migration is applied by the `migrate` init container in
-- `docker-compose.prod.yml` via dbmate. In development the bot skips this
-- path entirely — `db/manager.py` calls `create_tables()` which runs a
-- DROP + CREATE on every startup (`ENVIRONMENT=development` gate).
--
-- Keep `db/models.py::_CREATE_SQL` and this migration bit-for-bit identical.
-- Phase 7 adds a CI integration test that diffs the two automatically; for
-- now a manual parity check runs at the end of Phase 3.
--
-- Key design points preserved from Phase 2:
--   * All Discord ID columns are BIGINT (Discord snowflakes overflow 32-bit).
--   * `messages.guild_id` is intentionally NOT a foreign key — scraped
--     messages must survive guild_config churn; retention deletes messages
--     on a time axis independent of config.
--   * JSON columns are JSONB (not TEXT); the asyncpg JSONB codec is
--     registered in `db/manager._register_codecs` so query functions pass
--     real Python lists.
--   * `messages.timestamp` stays TEXT (stored as ISO string in
--     `DiscordMessage.timestamp: str` — changing this cascades into the
--     export pipeline).
--   * `is_pinned` is BOOLEAN (was INTEGER 0/1 in SQLite).
--   * `downloaded_media.media_id` is BIGINT GENERATED ALWAYS AS IDENTITY.

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
    -- job_id is the scrape that FIRST captured this message. Subsequent
    -- re-scrapes leave job_id alone (first-wins) so /pb export generate
    -- job_id:X returns that specific scrape's corpus. No FK: scrape_jobs
    -- and messages retain on the same wall-clock so orphan job_id strings
    -- are harmless.
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

-- migrate:down

-- Child-first drop order: scrape_jobs and downloaded_media both FK into
-- their parents. `messages` has no FK despite being a logical child of
-- guild_config (see _CREATE_SQL docstring for rationale).
DROP TABLE IF EXISTS downloaded_media;
DROP TABLE IF EXISTS scrape_jobs;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS guild_config;
