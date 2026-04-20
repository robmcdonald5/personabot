-- migrate:up

-- Initial Postgres schema for PersonaBot. Production applies this via the
-- `migrate` init container in `docker-compose.prod.yml` (dbmate). Dev skips
-- this path — `db/manager.py::create_tables` runs a DROP + CREATE on every
-- startup, gated on ENVIRONMENT=development.
--
-- `db/models.py::_CREATE_SQL` and this file must stay bit-for-bit identical;
-- `tests/integration/test_migrations.py` diffs them in CI.
--
-- Load-bearing design points:
--   * All Discord ID columns are BIGINT — INTEGER truncates 19-digit snowflakes.
--   * `messages.guild_id` is intentionally NOT a foreign key — scraped
--     messages must survive guild_config churn; retention deletes messages
--     on a time axis independent of config.
--   * JSON columns are JSONB; the asyncpg JSONB codec is registered in
--     `db/manager.register_codecs` so query functions pass real Python lists.
--   * `messages.timestamp` is TEXT (ISO string in `DiscordMessage.timestamp`
--     — changing this cascades into the export pipeline).

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
-- Supports the hourly retention sweep (delete_expired_messages).
CREATE INDEX idx_messages_created_at
    ON messages(created_at);

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
-- Supports the retention sweep (delete_expired_media).
CREATE INDEX idx_media_downloaded_at
    ON downloaded_media(downloaded_at);
-- Supports /pb db reset (reset_guild_data), which DELETEs by guild_id.
CREATE INDEX idx_media_guild
    ON downloaded_media(guild_id);

-- migrate:down

-- Child-first drop order: scrape_jobs and downloaded_media both FK into
-- their parents. `messages` has no FK despite being a logical child of
-- guild_config (see _CREATE_SQL docstring for rationale).
DROP TABLE IF EXISTS downloaded_media;
DROP TABLE IF EXISTS scrape_jobs;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS guild_config;
