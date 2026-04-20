# PersonaBot

Discord bot that scrapes user messages, processes them through a quality-filtering and scoring pipeline, and exports structured JSONL corpora for character analysis.

Companion project to [OnWriting](https://github.com/McDon/OnWriting) — PersonaBot handles data collection and preprocessing, OnWriting handles LLM-powered character generation.

## Quick Start

```bash
# Install dependencies
poetry install

# Start the dev Postgres container (port 5433, named volume)
docker compose -f docker-compose.dev.yml up -d

# Copy the env template and fill in your Discord token
cp .env.local.example .env.local
# Edit .env.local — DISCORD_TOKEN, DEV_GUILD_ID (optional), etc.

# Run the bot on the host (picks up .env.local)
set -a && source .env.local && set +a && poetry run python -m personabot.bot
```

`docs/docker-local-dev.md` has the full operational cheat sheet for both the hybrid dev flow above and the full-Docker prod-shape flow.

## Commands

All commands live under the `/pb` namespace:

| Command | Description |
|---------|-------------|
| `/pb config setup` | Chain channels → users → window in one sequential flow |
| `/pb config show` | Display current server configuration |
| `/pb config set-channels` | Set channels to scrape |
| `/pb config set-included-users` | Set the required user allowlist (empty = scrapes blocked) |
| `/pb config set-window` | Set the scrape date window (modal) |
| `/pb config set-message-limit` | Set the scrape message limit |
| `/pb config reset` | Reset all config fields to defaults |
| `/pb scrape start` | Start a message scrape using the saved guild config |
| `/pb scrape status <job_id>` | Check scrape job progress |
| `/pb scrape cancel <job_id>` | Cancel a running scrape |
| `/pb export generate [job_id]` | Export a user's corpus as JSONL (optionally scoped to one scrape) |
| `/pb export view <user>` | View stats for latest export |
| `/pb export list` | List scraped users and export status |
| `/pb stats user <user>` | User message stats |
| `/pb stats server` | Server-wide stats |
| `/pb stats top [n]` | Top users by message count |
| `/pb db reset` | Permanently wipe all scraped data for this server (keeps config) |

See `docs/commands.md` for the authoritative reference including parameters, permissions, and response details.

## Architecture

```
Collection → Storage → Preprocessing → JSONL Export
```

1. **Collection**: Scrape Discord messages via `channel.history()`, stamping each message with the `job_id` of the scrape that first captured it (first-wins — re-scrapes never reassign ownership).
2. **Storage**: PostgreSQL via an asyncpg connection pool (`min=2, max=10`). Schema owned by `dbmate` in production, `create_tables()` drop-and-recreate in development.
3. **Preprocessing**: Quality filters → weighted scoring → conversation windowing → token budget enforcement.
4. **Export**: JSONL corpus with conversation windows, optionally scoped to a specific `job_id` for a stable reproducible view of what one scrape discovered (feeds into OnWriting).

## Development

```bash
# Run tests (unit + integration — integration needs Postgres + dbmate)
poetry run pytest

# Run only unit tests (no external deps)
poetry run pytest -m "not integration"

# Run linters
poetry run ruff check src/ tests/
poetry run black --check src/ tests/
poetry run isort --check-only src/ tests/

# Type checking
poetry run mypy src/
```

Set `DEV_GUILD_ID` in `.env.local` for instant command sync during development (otherwise global sync takes up to 1 hour).

## Tech Stack

- Python 3.12+ with discord.py v2.5+
- PostgreSQL 17 via asyncpg (connection pool, JSONB columns for list fields)
- dbmate for production migrations; dev mode rebuilds schema on every restart
- pydantic-settings with a mandatory `ENVIRONMENT` gate (production + localhost DSN → `ValidationError`)
- Poetry for dependency management
- Docker Compose for local dev Postgres and production bundle
