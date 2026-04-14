"""PersonaBot Discord bot client."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiohttp
import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks

from personabot.config import Settings
from personabot.db import queries
from personabot.db.manager import DatabaseManager

logger = logging.getLogger(__name__)

# Cog extension module paths. The group name embedded in each cog's
# app_commands.Group must match the last path component; setup_hook uses
# this to re-parent cog groups under /pb after load_extension registers
# them at top level. Cross-cog parent sharing is explicitly not supported
# by discord.py (Rapptz #8069) — this tree manipulation is the only
# approach that works without collapsing into a single GroupCog.
_COG_EXTENSIONS: tuple[str, ...] = (
    "personabot.bot.cogs.config",
    "personabot.bot.cogs.scrape",
    "personabot.bot.cogs.export",
    "personabot.bot.cogs.stats",
    "personabot.bot.cogs.db",
)
_COG_GROUP_NAMES: tuple[str, ...] = (
    "config",
    "scrape",
    "export",
    "stats",
    "db",
)

# Docker HEALTHCHECK looks for this sentinel file. on_ready touches it when
# the bot is fully connected; on_disconnect removes it. The Dockerfile's
# HEALTHCHECK directive tests its existence — no HTTP surface needed.
_HEALTH_SENTINEL = Path("/tmp/healthy")


class PersonaBot(commands.Bot):
    """Main bot class with database and settings integration."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        settings: Settings,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix="!pb-unused-",  # Slash commands only; prefix required by lib
            intents=intents,
        )

        self.db_manager = db_manager
        self.settings = settings
        # Lazy-initialized on first healthcheck ping. One process-wide
        # session beats recreating one per retention tick — aiohttp docs
        # explicitly warn against the short-lived-session pattern.
        self._http_session: aiohttp.ClientSession | None = None

        # Shared /pb parent group. Cog subgroups are re-parented under this
        # in setup_hook since discord.py forbids cross-cog parent references.
        self.pb = app_commands.Group(
            name="pb", description="PersonaBot commands", guild_only=True
        )

    @asynccontextmanager
    async def db_conn(self) -> AsyncIterator[asyncpg.Connection]:
        """Acquire a pooled DB connection.

        Usage::

            async with self.bot.db_conn() as db:                    # read-only
                row = await queries.get_guild_config(db, guild_id)

            async with self.bot.db_conn() as db, db.transaction():  # write
                await queries.upsert_guild_config(db, ...)
        """
        async with self.db_manager.db_conn() as conn:
            yield conn

    async def save_guild_fields(self, guild: discord.Guild, **fields: Any) -> None:
        """Upsert one or more guild_config fields in a fresh transaction.

        Collapses the "acquire + transaction + upsert_guild_config"
        pattern shared by every picker callback in ``cogs/config.py``
        and ``cogs/scrape.py``.
        """
        async with self.db_conn() as db, db.transaction():
            await queries.upsert_guild_config(
                db, guild_id=guild.id, guild_name=guild.name, **fields
            )

    async def setup_hook(self) -> None:
        """Called before the bot connects. Load DB and cogs."""
        await self.db_manager.connect()
        logger.info("Database connected.")

        for ext in _COG_EXTENSIONS:
            await self.load_extension(ext)
            logger.info("Loaded extension: %s", ext)

        for name in _COG_GROUP_NAMES:
            cmd = self.tree.remove_command(name)
            if cmd is not None:
                self.pb.add_command(cmd)
        self.tree.add_command(self.pb)

        self.retention_cleanup.start()

        await self._sync_command_tree()

    async def _sync_command_tree(self) -> None:
        """Sync the app command tree to Discord.

        Dev mode (DEV_GUILD_ID set): always syncs to the dev guild — guild
        syncs are instant and not rate-limited.

        Production (no DEV_GUILD_ID): only syncs globally if
        auto_sync_commands is True. Global syncs are rate-limited and
        should be triggered manually when commands change, not on every
        restart. See AbstractUmbra's app command guide for rationale.
        """
        if self.settings.dev_guild_id:
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Commands synced to dev guild %s", self.settings.dev_guild_id)
            return

        if not self.settings.auto_sync_commands:
            logger.info(
                "Skipping global command sync (auto_sync_commands=False). "
                "Run a manual sync when command definitions change."
            )
            return

        logger.warning(
            "Global command sync running on startup. Global syncs are "
            "rate-limited; set auto_sync_commands=False and trigger manually "
            "when commands change."
        )
        await self.tree.sync()
        logger.info("Commands synced globally")

    async def close(self) -> None:
        """Clean shutdown."""
        self.retention_cleanup.cancel()
        _HEALTH_SENTINEL.unlink(missing_ok=True)
        if self._http_session is not None:
            await self._http_session.close()
            self._http_session = None
        await self.db_manager.close()
        logger.info("Database closed.")
        await super().close()

    async def on_ready(self) -> None:
        """Log when the bot is ready and mark the container healthy."""
        logger.info(
            "Logged in as %s (ID: %s)",
            self.user,
            self.user.id if self.user else "?",
        )
        # Touch the sentinel file so the Dockerfile HEALTHCHECK reports
        # healthy. This fires every time discord.py raises on_ready, which
        # happens after gateway resumption as well, so reconnect recoveries
        # re-mark healthy automatically.
        try:
            _HEALTH_SENTINEL.touch(exist_ok=True)
        except OSError as e:
            # /tmp should always be writable in Docker, but don't crash
            # the bot over a healthcheck corner case on dev machines.
            logger.warning("Could not touch health sentinel: %s", e)

    async def on_disconnect(self) -> None:
        """Clear the health sentinel on gateway disconnect.

        Paired with ``on_ready`` above. If discord.py's reconnect logic
        drops us (rate-limit, token invalidation, network blip), Docker's
        next healthcheck tick sees the missing sentinel and marks the
        container unhealthy. An automatic reconnect will fire on_ready
        again and re-mark healthy.
        """
        _HEALTH_SENTINEL.unlink(missing_ok=True)

    @tasks.loop(hours=1)
    async def retention_cleanup(self) -> None:
        """Purge scraped data older than retention_hours.

        Runs all SQL under one transaction on a single acquired connection,
        releases it, then does the filesystem sweep in a thread with no DB
        connection held — so the multi-second filesystem I/O doesn't pin a
        pool slot and starve other writers.
        """
        try:
            cutoff_dt = datetime.now(timezone.utc) - timedelta(
                hours=self.settings.retention_hours
            )

            async with self.db_conn() as db, db.transaction():
                expired_paths = await queries.delete_expired_media(db, cutoff_dt)
                msg_count = await queries.delete_expired_messages(db, cutoff_dt)
                job_count = await queries.delete_expired_scrape_jobs(db, cutoff_dt)

            cutoff_ts = cutoff_dt.timestamp()
            export_count = await self._cleanup_expired_files(
                expired_paths, cutoff_ts, self.settings.exports_dir
            )

            if expired_paths or msg_count or job_count or export_count:
                logger.info(
                    "Retention cleanup: %d media, %d messages, %d jobs, "
                    "%d export files deleted",
                    len(expired_paths),
                    msg_count,
                    job_count,
                    export_count,
                )

            # Dead-man-switch ping on clean ticks only — exceptions are
            # caught below and skip the ping so Healthchecks.io registers
            # the miss and alerts.
            await self._ping_healthcheck()

        except Exception:
            logger.exception("Retention cleanup failed")

    async def _ping_healthcheck(self) -> None:
        """GET settings.healthcheck_url with a hard 5s timeout.

        A failed ping logs a warning and returns, so Healthchecks.io ping
        failures never prevent the next retention tick from firing.
        Reuses the process-wide ``self._http_session``, creating it on
        first call.
        """
        url = self.settings.healthcheck_url
        if not url:
            return
        if self._http_session is None:
            self._http_session = aiohttp.ClientSession()
        try:
            await self._http_session.get(url, timeout=aiohttp.ClientTimeout(total=5))
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning("Healthchecks.io ping failed", exc_info=True)

    @staticmethod
    async def _cleanup_expired_files(
        expired_paths: list[str], cutoff_ts: float, exports_dir: Path
    ) -> int:
        """Delete expired media files and export files. Runs I/O in a thread."""

        def _do_cleanup() -> int:
            for path_str in expired_paths:
                p = Path(path_str)
                p.unlink(missing_ok=True)
                try:
                    p.parent.rmdir()
                except OSError:
                    pass

            # rglob on a missing directory yields an empty iterator, so no
            # pre-check is needed.
            export_count = 0
            for jsonl_file in exports_dir.rglob("*.jsonl"):
                if jsonl_file.stat().st_mtime < cutoff_ts:
                    jsonl_file.unlink()
                    export_count += 1
                    try:
                        jsonl_file.parent.rmdir()
                    except OSError:
                        pass
            return export_count

        return await asyncio.to_thread(_do_cleanup)

    @retention_cleanup.before_loop
    async def before_retention_cleanup(self) -> None:
        # tasks.loop(hours=1) waits an hour before its first iteration,
        # so on bot restarts more frequent than an hour no cleanup would
        # ever run. wait_until_ready() gates on the gateway connection;
        # the first tick then happens immediately.
        await self.wait_until_ready()
