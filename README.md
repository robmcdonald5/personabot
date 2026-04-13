# PersonaBot

Discord bot that scrapes user messages, processes them through a quality-filtering and scoring pipeline, and exports structured JSONL corpora for character analysis.

Companion project to [OnWriting](https://github.com/McDon/OnWriting) — PersonaBot handles data collection and preprocessing, OnWriting handles LLM-powered character generation.

## Quick Start

```bash
# Install dependencies
poetry install

# Copy and fill in environment variables
cp .env.example .env
# Edit .env with your Discord token

# Run the bot
poetry run python -m personabot.bot
```

## Commands

All commands live under the `/pb` namespace:

| Command | Description |
|---------|-------------|
| `/pb config show` | Display current server configuration |
| `/pb config set-channels` | Set channels to scrape |
| `/pb config set-included-users` | Set the required user allowlist (empty = scrapes blocked) |
| `/pb config set-window` | Set the default scrape date window (modal) |
| `/pb config set-message-limit` | Set default scrape limit |
| `/pb config reset` | Reset all config fields to defaults |
| `/pb scrape start [user] [channel] [limit]` | Start a message scrape job (opens date-window modal) |
| `/pb scrape status <job_id>` | Check scrape job progress |
| `/pb scrape cancel <job_id>` | Cancel a running scrape |
| `/pb export generate <user> [budget]` | Export a user's corpus as JSONL |
| `/pb export view <user>` | View stats for latest export |
| `/pb export list` | List scraped users and export status |
| `/pb stats user <user>` | User message stats |
| `/pb stats server` | Server-wide stats |
| `/pb stats top [n]` | Top users by message count |

See `docs/commands.md` for the authoritative reference including parameters, permissions, and response details.

## Architecture

```
Collection → Storage → Preprocessing → JSONL Export
```

1. **Collection**: Scrape Discord messages via `channel.history()`
2. **Storage**: SQLite via aiosqlite with batch upserts
3. **Preprocessing**: Quality filters → weighted scoring → conversation windowing → token budget
4. **Export**: JSONL corpus with conversation windows (feeds into OnWriting)

## Development

```bash
# Run tests
poetry run pytest

# Run linters
poetry run ruff check src/ tests/
poetry run black --check src/ tests/
poetry run isort --check-only src/ tests/

# Type checking
poetry run mypy src/
```

Set `DEV_GUILD_ID` in `.env` for instant command sync during development (otherwise global sync takes up to 1 hour).

## Tech Stack

- Python 3.12+ with discord.py v2.5+
- SQLite via aiosqlite
- pydantic-settings for configuration
- Poetry for dependency management
