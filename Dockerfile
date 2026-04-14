# Multi-stage build for PersonaBot.
#
# Stage 1 (builder):
#   - python:3.12-slim-bookworm: Debian-based slim image. Alpine (musl) is
#     avoided because asyncpg and Pydantic publish only glibc wheels; an
#     Alpine build would fall back to a from-source compile that adds ~8
#     minutes to CI and pulls in a heavy toolchain.
#   - Poetry 1.8.3 installed via pip; a virtualenv is materialized inside
#     the build context at /app/.venv via POETRY_VIRTUALENVS_IN_PROJECT.
#   - `poetry install --only main --no-root` installs runtime deps from
#     poetry.lock, without installing the personabot package itself. The
#     src/ tree is copied directly into the runtime stage — we do not
#     rebuild wheels for the project package, we just ship the source.
#
# Stage 2 (runtime):
#   - Same base image (NOT distroless) so `exec` into the container for
#     debugging still works — this is a bot, not a latency-critical API,
#     and prod debuggability is worth the ~30 MB image size delta.
#   - Copies ONLY the built .venv and src/ from the builder. No Poetry,
#     no build toolchain, no test files, no .git.
#   - Runs as a non-root user (UID/GID from `groupadd -r`). /tmp is
#     writable for the healthcheck sentinel file that client.py touches.
#   - HEALTHCHECK polls /tmp/healthy which bot/client.py creates in
#     on_ready and removes in on_disconnect. If the bot's gateway drops,
#     the sentinel disappears and Docker marks the container unhealthy.

FROM python:3.12-slim-bookworm AS builder

ENV POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install "poetry==$POETRY_VERSION"

WORKDIR /app

# Copy only lock/manifest first so the dependency layer caches unless
# pyproject.toml or poetry.lock changes. Source changes won't invalidate
# this layer.
COPY pyproject.toml poetry.lock ./

RUN poetry install --only main --no-root

# Source goes in a later layer — it changes on every commit, but deps
# typically don't. This ordering keeps CI builds fast after the first run.
COPY src/ ./src/


FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src"

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Non-root user. Running as root inside a container is no longer
# considered acceptable for any image that ships to production — this
# is belt-and-braces alongside the Cloud Firewall blocking inbound
# traffic to the VM.
RUN groupadd -r personabot && useradd -r -g personabot -m personabot

# The /app/data directory is where media + exports land. The compose
# file bind-mounts it to a named volume so files survive container
# recreation. Owned by the non-root user so the bot can write to it.
RUN mkdir -p /app/data && chown -R personabot:personabot /app/data

USER personabot

# The sentinel file is touched by bot/client.py on_ready and unlinked
# by on_disconnect. If discord.py's reconnect loop drops the gateway
# for more than 5 minutes (5 retries * 60s interval), Docker marks
# this container as unhealthy and the orchestrator can restart it.
HEALTHCHECK --interval=60s --timeout=10s --retries=5 --start-period=30s \
    CMD test -f /tmp/healthy || exit 1

ENTRYPOINT ["python", "-m", "personabot.bot"]
