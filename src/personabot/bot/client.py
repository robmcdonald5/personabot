"""PersonaBot Discord bot client."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
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
)
_COG_GROUP_NAMES: tuple[str, ...] = ("config", "scrape", "export", "stats")


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

        # Shared /pb parent group. Cog subgroups are re-parented under this
        # in setup_hook since discord.py forbids cross-cog parent references.
        self.pb = app_commands.Group(
            name="pb", description="PersonaBot commands", guild_only=True
        )

    @property
    def db(self) -> aiosqlite.Connection:
        """Shortcut for the shared aiosqlite connection."""
        return self.db_manager.get_connection()

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
        await self.db_manager.close()
        logger.info("Database closed.")
        await super().close()

    async def on_ready(self) -> None:
        """Log when the bot is ready."""
        logger.info(
            "Logged in as %s (ID: %s)",
            self.user,
            self.user.id if self.user else "?",
        )

    @tasks.loop(hours=1)
    async def retention_cleanup(self) -> None:
        """Purge scraped data older than retention_hours."""
        try:
            db = self.db
            cutoff_dt = datetime.now(timezone.utc) - timedelta(
                hours=self.settings.retention_hours
            )
            cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
            cutoff_ts = cutoff_dt.timestamp()

            # 1. Delete expired media (must be before messages — FK constraint)
            expired_paths = await queries.delete_expired_media(db, cutoff)

            # 2. Delete expired messages
            msg_count = await queries.delete_expired_messages(db, cutoff)
            await db.commit()  # Commit media + message deletions

            # 3. Delete expired scrape jobs + 4. Clean up expired export files
            # Run concurrently (no FK dependency between jobs and exports)
            job_count, export_count = await asyncio.gather(
                queries.delete_expired_scrape_jobs(db, cutoff),
                self._cleanup_expired_files(
                    expired_paths, cutoff_ts, self.settings.exports_dir
                ),
            )
            await db.commit()  # Commit job deletions

            if expired_paths or msg_count or job_count or export_count:
                logger.info(
                    "Retention cleanup: %d media, %d messages, %d jobs, "
                    "%d export files deleted",
                    len(expired_paths),
                    msg_count,
                    job_count,
                    export_count,
                )

        except Exception:
            logger.exception("Retention cleanup failed")

    @staticmethod
    async def _cleanup_expired_files(
        expired_paths: list[str], cutoff_ts: float, exports_dir: Path
    ) -> int:
        """Delete expired media files and export files. Runs I/O in a thread."""

        def _do_cleanup() -> int:
            # Clean up media files from expired DB records
            for path_str in expired_paths:
                p = Path(path_str)
                p.unlink(missing_ok=True)
                try:
                    p.parent.rmdir()
                except OSError:
                    pass

            # Clean up expired export files
            export_count = 0
            if exports_dir.exists():
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
