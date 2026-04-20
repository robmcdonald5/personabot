"""Scrape cog — /pb scrape commands for message collection."""

import asyncio
import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from personabot.bot.client import PersonaBot
from personabot.bot.views import date_to_utc_midnight, parse_date_ymd
from personabot.db import queries
from personabot.schemas.discord import DiscordMessage, JobStatus

logger = logging.getLogger(__name__)


async def fetch_job_choices(
    bot: PersonaBot,
    interaction: discord.Interaction,
    current: str,
    *,
    status_filter: JobStatus | None = None,
    predicate: Callable[[queries.ScrapeJob], bool] | None = None,
    label: Callable[[queries.ScrapeJob], str] = lambda j: (
        f"{j.job_id} -- {j.status} ({j.messages_found:,} found)"
    ),
) -> list[app_commands.Choice[str]]:
    """Build an autocomplete list of recent scrape jobs.

    Shared by scrape's own `_status_autocomplete` / `_cancel_autocomplete`
    and export's `_job_autocomplete`. ``predicate`` is an optional post-
    fetch filter (export uses it to hide jobs with zero stored rows);
    ``label`` formats each Choice name. DB errors return an empty list
    so a transient failure just shows "no matches" instead of breaking
    the UI.
    """
    if interaction.guild_id is None:
        return []
    try:
        async with bot.db_conn() as db:
            jobs = await queries.get_recent_scrape_jobs(
                db, interaction.guild_id, status_filter=status_filter
            )
    except Exception:
        logger.exception("Job autocomplete failed")
        return []
    return [
        app_commands.Choice(name=label(j), value=j.job_id)
        for j in jobs
        if (predicate is None or predicate(j)) and current.lower() in j.job_id.lower()
    ][:25]


class ScrapeCog(commands.Cog):
    """Message scraping and collection."""

    def __init__(self, bot: PersonaBot) -> None:
        self.bot = bot
        # job_id -> (task, cancel_event). The task ref keeps asyncio from
        # GC'ing the in-flight scrape; the event lets cog_unload (and
        # /pb scrape cancel) signal cooperative cancellation. Single dict
        # keeps the two refs from drifting out of sync.
        self._jobs: dict[str, tuple[asyncio.Task, asyncio.Event]] = {}

    async def cog_unload(self) -> None:
        """Signal cancellation and drain in-flight scrapes on reload/shutdown."""
        for _, event in self._jobs.values():
            event.set()
        if self._jobs:
            await asyncio.gather(
                *(task for task, _ in self._jobs.values()),
                return_exceptions=True,
            )
        self._jobs.clear()

    scrape = app_commands.Group(
        name="scrape",
        description="Message scraping commands",
        parent=None,
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @scrape.command(
        name="start",
        description="Run a message scrape using the saved guild config",
    )
    async def scrape_start(self, interaction: discord.Interaction) -> None:
        """Start a scrape using only the values saved via /pb config.

        Errors with a missing-fields list if channels, included users, or
        the scrape window are unset. No optional params — an operator who
        wants to change what gets scraped edits the config first, then
        re-runs this command.
        """
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        cfg = await self.bot.get_guild_config(guild.id)

        missing: list[str] = []
        if cfg is None or not cfg.scrape_channels:
            missing.append("channels (`/pb config set-channels`)")
        if cfg is None or not cfg.included_users:
            missing.append("included users (`/pb config set-included-users`)")
        if cfg is None or not cfg.scrape_start_date or not cfg.scrape_end_date:
            missing.append("scrape window (`/pb config set-window`)")

        if missing:
            await interaction.response.send_message(
                "Missing required configuration:\n- "
                + "\n- ".join(missing)
                + "\n\nRun `/pb config setup` to configure everything "
                "sequentially, or run the individual commands above.",
                ephemeral=True,
            )
            return

        # All required fields present — narrow cfg for the type checker.
        assert cfg is not None
        assert cfg.scrape_channels
        assert cfg.included_users
        assert cfg.scrape_start_date is not None
        assert cfg.scrape_end_date is not None

        try:
            start_d = parse_date_ymd(cfg.scrape_start_date, "Saved start date")
            end_d = parse_date_ymd(cfg.scrape_end_date, "Saved end date")
        except ValueError as e:
            await interaction.response.send_message(
                f"Saved scrape window is invalid: {e}\n"
                "Re-run `/pb config set-window` to fix it.",
                ephemeral=True,
            )
            return

        start_dt = date_to_utc_midnight(start_d)
        end_dt = date_to_utc_midnight(end_d + timedelta(days=1))
        allowed_ids: set[int] = set(cfg.included_users)
        resolved_limit = cfg.default_limit
        channel_ids = cfg.scrape_channels

        job_id = secrets.token_hex(6)
        async with self.bot.db_conn() as db, db.transaction():
            await queries.create_scrape_job(
                db,
                job_id=job_id,
                guild_id=guild.id,
                channels=channel_ids,
            )

        await interaction.response.send_message(
            f"Scrape job `{job_id}` started. "
            f"Target: {len(allowed_ids)} included users, "
            f"Channels: {len(channel_ids)}, "
            f"Window: `{start_d.isoformat()}` → `{end_d.isoformat()}`, "
            f"Limit: {resolved_limit:,}",
            ephemeral=True,
        )

        cancel_event = asyncio.Event()
        task = asyncio.create_task(
            self._run_scrape(
                job_id=job_id,
                guild=guild,
                channel_ids=channel_ids,
                allowed_ids=allowed_ids,
                limit=resolved_limit,
                start_dt=start_dt,
                end_dt=end_dt,
                notify_channel=interaction.channel,  # type: ignore[arg-type]
                cancel_event=cancel_event,
            )
        )
        self._jobs[job_id] = (task, cancel_event)

    async def _run_scrape(
        self,
        job_id: str,
        guild: discord.Guild,
        channel_ids: list[int],
        allowed_ids: set[int],
        limit: int,
        start_dt: datetime,
        end_dt: datetime,
        notify_channel: discord.abc.Messageable | None,
        cancel_event: asyncio.Event,
    ) -> None:
        """Background scrape task. Limit is global across all channels.

        `allowed_ids` is the precomputed set of user IDs whose messages
        should be collected. It is always non-empty — scrape_start either
        sets it from the `user:` param or from a non-empty include list
        (and blocks the scrape otherwise).

        `start_dt` and `end_dt` are the inclusive-start / exclusive-end UTC
        datetime bounds passed to `channel.history(after=..., before=...)`.
        discord.py auto-sets `oldest_first=True` when `after` is set, so the
        global `limit` caps from the oldest end of the window.

        Pool-hygiene note: every DB operation here uses its own short-lived
        ``async with self.bot.db_conn() as db, db.transaction():`` block.
        Holding one pool connection for the multi-minute scrape duration
        would starve other cog commands sharing the same max=10 pool.
        """
        media_threshold = self.bot.settings.media_reaction_threshold
        total_found = 0
        total_stored = 0
        batch: list[DiscordMessage] = []

        try:
            for channel_id in channel_ids:
                if cancel_event.is_set() or total_found >= limit:
                    break

                channel = guild.get_channel(channel_id)
                if channel is None or not isinstance(channel, discord.TextChannel):
                    continue

                logger.info("Scraping channel: %s (%s)", channel.name, channel.id)

                remaining = limit - total_found
                async for message in channel.history(
                    limit=remaining, after=start_dt, before=end_dt
                ):
                    if cancel_event.is_set():
                        break

                    if message.author.bot:
                        continue
                    if message.author.id not in allowed_ids:
                        continue

                    total_found += 1
                    content = message.content or ""
                    reaction_count = sum(r.count for r in message.reactions)

                    dm = DiscordMessage(
                        message_id=message.id,
                        guild_id=guild.id,
                        channel_id=channel.id,
                        channel_name=channel.name,
                        author_id=message.author.id,
                        author_name=message.author.display_name,
                        content=content,
                        timestamp=message.created_at.isoformat(),
                        reaction_count=reaction_count,
                        reply_to_id=(
                            message.reference.message_id if message.reference else None
                        ),
                        thread_id=(message.thread.id if message.thread else None),
                        is_pinned=message.pinned,
                        attachment_count=len(message.attachments),
                        embed_count=len(message.embeds),
                        word_count=len(content.split()),
                    )
                    batch.append(dm)

                    if dm.reaction_count >= media_threshold and message.attachments:
                        images = [
                            att
                            for att in message.attachments
                            if att.content_type
                            and att.content_type.startswith("image/")
                        ]
                        if images:
                            await asyncio.gather(
                                *(
                                    self._download_media(message.id, guild.id, att)
                                    for att in images
                                )
                            )

                    if len(batch) >= 100:
                        async with self.bot.db_conn() as db, db.transaction():
                            stored = await queries.upsert_messages_batch(
                                db, batch, job_id=job_id
                            )
                        total_stored += stored
                        batch.clear()

                    if total_found > 0 and total_found % 500 == 0:
                        async with self.bot.db_conn() as db, db.transaction():
                            await queries.update_scrape_job_status(
                                db,
                                job_id,
                                JobStatus.RUNNING,
                                messages_found=total_found,
                                messages_stored=total_stored,
                            )
                        if notify_channel is not None:
                            await notify_channel.send(
                                f"Scrape `{job_id}`: {total_found:,} messages "
                                f"found, {total_stored:,} stored..."
                            )

            # Flush remaining batch.
            if batch:
                async with self.bot.db_conn() as db, db.transaction():
                    stored = await queries.upsert_messages_batch(
                        db, batch, job_id=job_id
                    )
                total_stored += stored

            status = (
                JobStatus.CANCELLED if cancel_event.is_set() else JobStatus.COMPLETED
            )
            async with self.bot.db_conn() as db, db.transaction():
                await queries.update_scrape_job_status(
                    db,
                    job_id,
                    status,
                    messages_found=total_found,
                    messages_stored=total_stored,
                )

            if notify_channel is not None:
                msg = (
                    f"Scrape `{job_id}` {status}. "
                    f"Found: {total_found:,}, Stored: {total_stored:,}"
                )
                if status == JobStatus.COMPLETED:
                    hours = self.bot.settings.retention_hours
                    msg += (
                        f"\nRun `/pb export generate` within "
                        f"{hours} hours before data expires."
                    )
                await notify_channel.send(msg)

        except Exception as e:
            logger.error("Scrape job %s failed: %s", job_id, e)
            try:
                async with self.bot.db_conn() as db, db.transaction():
                    await queries.update_scrape_job_status(
                        db,
                        job_id,
                        JobStatus.FAILED,
                        error_message=str(e),
                        messages_found=total_found,
                        messages_stored=total_stored,
                    )
            except Exception:
                logger.exception("Failed to update job %s status to FAILED", job_id)
            if notify_channel is not None:
                try:
                    await notify_channel.send(f"Scrape `{job_id}` failed: {e}")
                except Exception:
                    pass

        finally:
            self._jobs.pop(job_id, None)

    async def _download_media(
        self,
        message_id: int,
        guild_id: int,
        attachment: discord.Attachment,
    ) -> None:
        """Download a media attachment to local storage and record it.

        Acquires its own short-lived pool connection for the DB insert —
        the caller (``_run_scrape``) does NOT pass a connection in. A
        download failure at the DB level is logged and swallowed so one
        bad media row does not abort an entire scrape batch.
        """
        local_dir = self.bot.settings.media_dir / str(guild_id) / str(message_id)
        await asyncio.to_thread(local_dir.mkdir, parents=True, exist_ok=True)
        safe_name = Path(attachment.filename).name  # Strip any directory components
        local_path = local_dir / safe_name

        try:
            await attachment.save(local_path)
        except discord.HTTPException as e:
            logger.warning("Failed to download media %s: %s", attachment.url, e)
            return

        try:
            async with self.bot.db_conn() as db, db.transaction():
                await queries.save_downloaded_media(
                    db,
                    message_id=message_id,
                    guild_id=guild_id,
                    original_url=attachment.url,
                    local_path=str(local_path),
                    content_type=attachment.content_type,
                    file_size=attachment.size,
                )
        except asyncpg.PostgresError as e:
            logger.warning("Failed to save media record for %s: %s", attachment.url, e)

    # discord.py's autocomplete callbacks must have exactly 2-3 params,
    # so these are thin wrappers around fetch_job_choices above.
    async def _status_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await fetch_job_choices(self.bot, interaction, current)

    async def _cancel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await fetch_job_choices(
            self.bot, interaction, current, status_filter=JobStatus.RUNNING
        )

    @scrape.command(name="status", description="Check scrape job status")
    @app_commands.describe(job_id="Job ID to check")
    @app_commands.autocomplete(job_id=_status_autocomplete)
    async def scrape_status(
        self, interaction: discord.Interaction, job_id: str
    ) -> None:
        async with self.bot.db_conn() as db:
            job = await queries.get_scrape_job(db, job_id)

        if job is None:
            await interaction.response.send_message(
                f"Job `{job_id}` not found.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"Scrape Job: {job.job_id}",
            color=(
                discord.Color.green()
                if job.status == JobStatus.COMPLETED
                else (
                    discord.Color.orange()
                    if job.status == JobStatus.RUNNING
                    else discord.Color.red()
                )
            ),
        )
        embed.add_field(name="Status", value=job.status, inline=True)
        embed.add_field(name="Found", value=f"{job.messages_found:,}", inline=True)
        embed.add_field(name="Stored", value=f"{job.messages_stored:,}", inline=True)
        if job.error_message:
            embed.add_field(name="Error", value=job.error_message, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @scrape.command(name="cancel", description="Cancel a running scrape job")
    @app_commands.describe(job_id="Job ID to cancel")
    @app_commands.autocomplete(job_id=_cancel_autocomplete)
    async def scrape_cancel(
        self, interaction: discord.Interaction, job_id: str
    ) -> None:
        entry = self._jobs.get(job_id)
        if entry is None:
            await interaction.response.send_message(
                f"Job `{job_id}` is not running.", ephemeral=True
            )
            return

        _, cancel_event = entry
        cancel_event.set()
        await interaction.response.send_message(
            f"Cancellation requested for job `{job_id}`.", ephemeral=True
        )


async def setup(bot: PersonaBot) -> None:
    await bot.add_cog(ScrapeCog(bot))
