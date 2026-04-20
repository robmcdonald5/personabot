# Docker Local Dev Reference

Cheat sheet for running PersonaBot + Postgres locally via Docker Compose. Two flows are supported:

- **Hybrid** — dev Postgres in Docker (`docker-compose.dev.yml`), bot runs on the host via `poetry run`. Fast iteration loop, schema drops on every bot restart.
- **Full-docker** — everything via `docker-compose.prod.yml` (postgres + dbmate migrate init container + bot image built from `Dockerfile`). Matches the Hetzner deploy shape, good for pre-ship rehearsals.

## Table of contents

- [Docker Desktop itself](#docker-desktop-itself)
- [Hybrid flow — dev Postgres only](#hybrid-flow--dev-postgres-only-bot-runs-on-host)
- [Full-docker flow — everything via prod compose](#full-docker-flow--everything-via-docker-composeprodyml)
- [Rebuilding after code changes](#rebuilding-after-code-changes-full-docker-flow-only)
- [Container inspection](#container-inspection)
- [Direct Postgres access (psql)](#direct-postgres-access-psql)
- [Manual migrations (dbmate)](#manual-migrations-dbmate-full-docker-flow)
- [Exec into a container](#exec-into-a-container-troubleshooting)
- [Volumes](#volumes)
- [Full cleanup](#full-cleanup-nuke-everything)
- [Troubleshooting quick hits](#troubleshooting-quick-hits)
- [Sanity check workflow](#sanity-check-workflow-run-after-every-significant-change)

---

## Docker Desktop itself

```bash
# Is Docker reachable right now?
docker ps
```

Docker Desktop must be running (GUI tray icon, white/green whale). If the command errors with `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified`, start Docker Desktop from the Start menu and wait for the whale to stop spinning, then retry.

---

## Hybrid flow — dev Postgres only, bot runs on host

This is the fastest iteration loop. Schema wipes on every bot restart because `ENVIRONMENT=development` gates the DROP + CREATE path in `db/models.py`.

```bash
# Start/stop dev Postgres (port 5433, so it won't collide with a system Postgres on 5432)
docker compose -f docker-compose.dev.yml up -d
docker compose -f docker-compose.dev.yml down

# Status + health
docker compose -f docker-compose.dev.yml ps

# Follow Postgres logs
docker compose -f docker-compose.dev.yml logs -f

# Start the bot on the host (after Postgres is up)
set -a && source .env.local && set +a && poetry run python -m personabot.bot
# Powershell
Get-Content .env.local | ForEach-Object { $name, $value = $_ -split '=', 2; if ($name -and $value) { [System.Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim()) } }; poetry run python -m personabot.bot

# Ctrl+C kills the bot; Postgres keeps running.
```

Wipe the dev Postgres volume entirely (kills all scraped data, container, and the named volume):

```bash
docker compose -f docker-compose.dev.yml down -v
```

---

## Full-docker flow — everything via `docker-compose.prod.yml`

Runs `postgres` + `migrate` (dbmate init container) + `bot` (built from `Dockerfile`). **Requires `.env.prod`** — create one from `.env.prod.example` first.

One important flag: `--env-file .env.prod` must be on **every** command that parses the compose file (including `ps`, `logs`, `down`), or Compose re-evaluates the `${POSTGRES_USER:?required}` gates and bails.

```bash
# Build + bring up the whole stack (first run or after code changes)
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

# Bring up without rebuilding (after env-only changes)
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d

# Bring down (stops + removes containers, network; keeps volumes)
docker compose -f docker-compose.prod.yml --env-file .env.prod down

# Bring down AND wipe volumes (scraped data + media + export files gone)
docker compose -f docker-compose.prod.yml --env-file .env.prod down -v

# Status
docker compose -f docker-compose.prod.yml --env-file .env.prod ps

# Follow bot logs only
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f bot

# Follow Postgres logs only
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f postgres

# Follow everything
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f
```

> **Note**: `ENVIRONMENT=production` in `.env.prod` means there's **no DROP on startup** — dbmate owns the schema. Data survives restarts. If you want "wipe on every restart" behavior, use the hybrid flow instead.

---

## Rebuilding after code changes (full-docker flow only)

The bot image is built from source at `up --build` time. Without `--build`, a stale image runs.

```bash
# Rebuild and restart the bot container only (postgres + migrate stay)
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build bot

# Rebuild the image without running containers (cache check only)
docker compose -f docker-compose.prod.yml --env-file .env.prod build bot

# Force a full rebuild ignoring layer cache (slow — only when deps change)
docker compose -f docker-compose.prod.yml --env-file .env.prod build --no-cache bot
```

---

## Container inspection

```bash
# All running containers
docker ps

# All containers including stopped
docker ps -a

# One container's health status
docker inspect personabot_pg_dev --format '{{.State.Health.Status}}'

# One container's IP + ports
docker inspect personabot_pg_dev --format '{{.NetworkSettings.IPAddress}} {{range .NetworkSettings.Ports}}{{.}}{{end}}'

# Recent logs without following (last 50 lines)
docker logs --tail 50 personabot_pg_dev
```

---

## Direct Postgres access (psql)

**Dev flow** (container name from `docker-compose.dev.yml`):

```bash
# Interactive psql shell inside the dev container
docker exec -it personabot_pg_dev psql -U personabot -d personabot

# One-shot query
docker exec personabot_pg_dev psql -U personabot -d personabot -c "SELECT count(*) FROM messages;"

# From the host (requires a psql client installed on Windows; uses the loopback port)
psql postgresql://personabot:dev@localhost:5433/personabot
```

**Full-docker flow** (auto-generated container name):

```bash
# Find the container name first
docker compose -f docker-compose.prod.yml --env-file .env.prod ps

# Then exec (replace with actual name, typically personabot-postgres-1)
docker exec -it personabot-postgres-1 psql -U personabot -d personabot
```

**Useful queries** (state debugging):

```sql
-- List tables
\dt

-- Row counts at a glance
SELECT 'guild_config' AS t, count(*) FROM guild_config
UNION ALL SELECT 'scrape_jobs', count(*) FROM scrape_jobs
UNION ALL SELECT 'messages', count(*) FROM messages
UNION ALL SELECT 'downloaded_media', count(*) FROM downloaded_media;

-- Guild config (for debugging scrape_start missing-field errors)
SELECT guild_id, guild_name, scrape_channels, included_users,
       scrape_start_date, scrape_end_date, default_limit
FROM guild_config;

-- Recent scrape jobs
SELECT job_id, status, messages_found, messages_stored, created_at, error_message
FROM scrape_jobs ORDER BY created_at DESC LIMIT 10;

-- Quit psql
\q
```

---

## Manual migrations (dbmate, full-docker flow)

The `migrate` init container runs automatically on `up`, but you can invoke dbmate manually against a running Postgres for debugging. **On Git Bash you need `MSYS_NO_PATHCONV=1`** or the `/db/migrations` path gets rewritten to `C:/Program Files/Git/db/migrations`.

```bash
# Apply all pending migrations
MSYS_NO_PATHCONV=1 docker run --rm \
  --network personabot_default \
  -v "C:/Users/McDon/Repos/personabot/db:/db" \
  -e DATABASE_URL="postgres://personabot:dev@personabot_pg_dev:5432/personabot?sslmode=disable" \
  ghcr.io/amacneil/dbmate:latest \
  --migrations-dir /db/migrations up

# Roll back the most recent migration
MSYS_NO_PATHCONV=1 docker run --rm \
  --network personabot_default \
  -v "C:/Users/McDon/Repos/personabot/db:/db" \
  -e DATABASE_URL="postgres://personabot:dev@personabot_pg_dev:5432/personabot?sslmode=disable" \
  ghcr.io/amacneil/dbmate:latest \
  --migrations-dir /db/migrations down

# Check migration status
MSYS_NO_PATHCONV=1 docker run --rm \
  --network personabot_default \
  -v "C:/Users/McDon/Repos/personabot/db:/db" \
  -e DATABASE_URL="postgres://personabot:dev@personabot_pg_dev:5432/personabot?sslmode=disable" \
  ghcr.io/amacneil/dbmate:latest \
  --migrations-dir /db/migrations status
```

Replace `personabot_pg_dev` with the prod-compose Postgres container name if running that flow, and the network with `personabot_default` (dev) or `personabot-compose_default` (prod, name varies by project).

---

## Exec into a container (troubleshooting)

```bash
# Open a shell inside the dev Postgres (Alpine uses sh, not bash)
docker exec -it personabot_pg_dev sh

# Inside the container: check the Postgres config, data dir, etc.
ls /var/lib/postgresql/data
cat /etc/postgresql.conf

# Open a shell inside the running bot container (prod compose)
docker exec -it personabot-bot-1 sh

# The bot runs as non-root user `personabot`
whoami
ls /app/src/personabot/
```

---

## Volumes

Named volumes persist across `up`/`down` cycles — only `down -v` removes them.

```bash
# List all volumes
docker volume ls

# Inspect a specific volume (mount point, driver, labels)
docker volume inspect personabot_pgdata_dev

# Remove a specific volume (container must be stopped first)
docker volume rm personabot_pgdata_dev

# Prune ALL unused volumes (danger: removes everything not attached to a running container)
docker volume prune
```

---

## Full cleanup (nuke everything)

```bash
# Stop + remove all PersonaBot containers + volumes (both flows)
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.prod.yml --env-file .env.prod down -v

# Remove the bot image (force rebuild next up)
docker image rm personabot-bot

# System-wide prune: stopped containers, unused networks, dangling images, build cache
docker system prune

# System-wide prune + volumes (nuclear option)
docker system prune --volumes
```

---

## Troubleshooting quick hits

| Symptom | Cause | Fix |
|---|---|---|
| `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified` | Docker Desktop not running | Start Docker Desktop from Start menu |
| `bind: address already in use` | Port 5432 taken by a system Postgres | Stop the other Postgres or use the dev compose (5433) |
| `required variable POSTGRES_USER is missing a value` | Forgot `--env-file .env.prod` on a prod-compose command | Add the flag; it's required on every subcommand that parses the file |
| `error during connect: ... /pipe/dockerDesktopLinuxEngine` on `docker ps` | Docker Desktop crashed or restarting | Restart Docker Desktop |
| `Container personabot_pg_dev is unhealthy` after start | Postgres failed to initialize | `docker logs personabot_pg_dev` to see why; usually bad env vars or corrupt volume |
| `could not find migrations directory 'C:/Program Files/Git/db/migrations'` | Git Bash path translation | Prefix with `MSYS_NO_PATHCONV=1` |
| Bot image runs old code after rebuild | Cached layer | `up -d --build` (not just `up -d`) |
| `.env.local` edits don't apply | Already-running bot process | Kill bot, re-source, re-run |

---

## Sanity check workflow (run after every significant change)

```bash
# 1. Is Docker up?
docker ps

# 2. Is dev Postgres up and healthy?
docker compose -f docker-compose.dev.yml ps

# 3. Can the tests reach it?
cd C:/Users/McDon/Repos/personabot
TEST_DATABASE_URL="postgresql://personabot:dev@localhost:5433/personabot" poetry run pytest

# 4. Does the bot boot cleanly?
set -a && source .env.local && set +a && poetry run python -m personabot.bot
# Expected: "Database pool created", "Schema reset", "Logged in as personabot#6949"
# Ctrl+C to stop.
```

If all four pass, you're in a known-good state.
