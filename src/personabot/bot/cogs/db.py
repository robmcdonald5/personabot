"""DB cog — /pb db commands for server-scoped data management."""

import asyncio
import shutil

import discord
from discord import app_commands
from discord.ext import commands

from personabot.bot.client import PersonaBot
from personabot.bot.views import ResetConfirmView
from personabot.db import queries
from personabot.schemas.discord import JobStatus


class DbCog(commands.Cog):
    """Server-scoped data management — destructive wipes of scraped data."""

    def __init__(self, bot: PersonaBot) -> None:
        self.bot = bot

    db = app_commands.Group(
        name="db",
        description="Server data management",
        parent=None,
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @db.command(
        name="reset",
        description="Permanently delete all scraped data for this server (keeps config)",
    )
    async def db_reset(self, interaction: discord.Interaction) -> None:
        """Permanently wipe this guild's messages, scrape jobs, media, and exports.

        Preserves ``guild_config`` — the operator keeps their channels,
        included users, window, and limit. Use ``/pb config reset``
        separately to clear settings.

        Refuses to run while any scrape is in flight for this guild; a
        live scrape would race the delete and write orphaned rows after
        the wipe. The operator must cancel or wait for running jobs
        first.
        """
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        # Hard gate: any running scrape for this guild blocks the reset.
        async with self.bot.db_conn() as db:
            running = await queries.get_recent_scrape_jobs(
                db, guild.id, status_filter=JobStatus.RUNNING
            )
        if running:
            running_ids = ", ".join(f"`{j.job_id}`" for j in running)
            await interaction.response.send_message(
                f"Cannot reset — scrape job(s) still running: {running_ids}.\n"
                "Run `/pb scrape cancel <job_id>` first, then retry.",
                ephemeral=True,
            )
            return

        async def run_reset() -> str:
            async with self.bot.db_conn() as db, db.transaction():
                media_paths, msg_count, job_count = await queries.reset_guild_data(
                    db, guild.id
                )
            # Filesystem sweep runs AFTER the transaction commits and
            # the pool connection is released — disk I/O never holds a
            # pool slot. to_thread keeps the event loop hot.
            await asyncio.to_thread(self._cleanup_guild_tree, guild.id)
            if msg_count == 0 and job_count == 0 and not media_paths:
                return (
                    "No scraped data to reset — this server has nothing "
                    "stored. Server configuration is untouched."
                )
            return (
                "Server data reset complete.\n"
                f"- Messages deleted: **{msg_count:,}**\n"
                f"- Scrape jobs deleted: **{job_count:,}**\n"
                f"- Media files deleted: **{len(media_paths):,}**\n"
                "- Export files removed from disk\n"
                "Server configuration preserved — use `/pb config reset` "
                "to clear settings."
            )

        view = ResetConfirmView(
            author_id=interaction.user.id,
            on_reset=run_reset,
            button_label="Delete Server Data",
        )
        await interaction.response.send_message(
            "**DANGER — this permanently deletes ALL scraped data for this server:**\n"
            "- Every stored message\n"
            "- Every scrape job record\n"
            "- Every downloaded media file\n"
            "- Every generated export\n\n"
            "Server configuration (channels, included users, window) is "
            "**not** affected — use `/pb config reset` for that.\n\n"
            "**This cannot be undone. Are you sure?**",
            view=view,
            ephemeral=True,
        )
        view.message = await interaction.original_response()

    def _cleanup_guild_tree(self, guild_id: int) -> None:
        """Blocking filesystem sweep — runs in ``asyncio.to_thread``.

        Removes the per-guild media and export subtrees wholesale. No
        per-file path list needed: unlike ``retention_cleanup`` (which
        must leave unexpired files alone), reset nukes the entire
        guild subtree so the returned ``local_path`` values from the
        DB delete would be fully redundant with ``rmtree``'s walk —
        and ``rmtree`` additionally catches stragglers from crashed
        scrapes that never reached the DB. ``ignore_errors=True`` is
        idempotent: Windows file locks or transient failures will be
        cleaned up on the next reset.
        """
        settings = self.bot.settings
        for tree in (
            settings.media_dir / str(guild_id),
            settings.exports_dir / str(guild_id),
        ):
            shutil.rmtree(tree, ignore_errors=True)


async def setup(bot: PersonaBot) -> None:
    await bot.add_cog(DbCog(bot))
