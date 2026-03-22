"""Scrape cog — /pb scrape commands for message collection."""

import asyncio
import logging
import uuid
from pathlib import Path

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from personabot.bot.client import PersonaBot
from personabot.db import queries
from personabot.schemas.discord import DiscordMessage, JobStatus

logger = logging.getLogger(__name__)


class ScrapeCog(commands.Cog):
    """Message scraping and collection."""

    def __init__(self, bot: PersonaBot) -> None:
        self.bot = bot
        # Track running tasks to prevent garbage collection
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    scrape = app_commands.Group(
        name="scrape",
        description="Message scraping commands",
        parent=None,
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @scrape.command(name="start", description="Start a message scrape job")
    @app_commands.describe(
        user="Target user (optional — scrape all if omitted)",
        channel="Specific channel (optional — uses configured channels)",
        limit="Max messages to scrape (optional)",
    )
    async def scrape_start(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
        channel: discord.TextChannel | None = None,
        limit: int | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        db = self.bot.db_manager.get_connection()

        # Fetch config once for channel, limit, and excluded users
        cfg = await queries.get_guild_config(db, guild.id)

        # Determine channels to scrape
        if channel:
            channel_ids = [channel.id]
        elif cfg and cfg.scrape_channels:
            channel_ids = cfg.scrape_channels
        else:
            await interaction.response.send_message(
                "No channels configured. Use `/pb config set-channels` first, "
                "or specify a channel.",
                ephemeral=True,
            )
            return

        # Determine limit (reuse cfg from above)
        if limit is None:
            limit = cfg.default_limit if cfg else self.bot.settings.default_limit

        # Snapshot excluded users from config
        excluded_users = set(cfg.excluded_users) if cfg else set()

        # Create job record
        job_id = str(uuid.uuid4())[:12]
        await queries.upsert_guild_config(db, guild.id, guild.name)
        await queries.create_scrape_job(
            db,
            job_id=job_id,
            guild_id=guild.id,
            target_user_id=user.id if user else None,
            channels=channel_ids,
        )
        await db.commit()

        await interaction.response.send_message(
            f"Scrape job `{job_id}` started. "
            f"Target: {user.mention if user else 'all users'}, "
            f"Channels: {len(channel_ids)}, Limit: {limit:,}",
            ephemeral=True,
        )

        # Launch background task
        cancel_event = asyncio.Event()
        self._cancel_events[job_id] = cancel_event

        task = asyncio.create_task(
            self._run_scrape(
                job_id=job_id,
                guild=guild,
                channel_ids=channel_ids,
                target_user_id=user.id if user else None,
                limit=limit,
                excluded_users=excluded_users,
                notify_channel=interaction.channel,  # type: ignore[arg-type]
                cancel_event=cancel_event,
            )
        )
        self._tasks[job_id] = task

    async def _run_scrape(
        self,
        job_id: str,
        guild: discord.Guild,
        channel_ids: list[int],
        target_user_id: int | None,
        limit: int,
        excluded_users: set[int],
        notify_channel: discord.abc.Messageable | None,
        cancel_event: asyncio.Event,
    ) -> None:
        """Background scrape task. Limit is global across all channels."""
        db = self.bot.db_manager.get_connection()
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
                async for message in channel.history(limit=remaining):
                    if cancel_event.is_set():
                        break

                    # Skip bots and excluded users
                    if message.author.bot or message.author.id in excluded_users:
                        continue

                    # If targeting a specific user, skip others
                    if target_user_id and message.author.id != target_user_id:
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

                    # Download media for messages meeting reaction threshold
                    if (
                        dm.reaction_count >= self.bot.settings.media_reaction_threshold
                        and message.attachments
                    ):
                        for att in message.attachments:
                            if att.content_type and att.content_type.startswith(
                                "image/"
                            ):
                                await self._download_media(
                                    db, message.id, guild.id, att
                                )

                    # Batch upsert every 100 messages
                    if len(batch) >= 100:
                        stored = await queries.upsert_messages_batch(db, batch)
                        total_stored += stored
                        batch.clear()
                        await db.commit()

                    # Progress update every 500 messages
                    if total_found > 0 and total_found % 500 == 0:
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

            # Flush remaining batch
            if batch:
                stored = await queries.upsert_messages_batch(db, batch)
                total_stored += stored

            status = (
                JobStatus.CANCELLED if cancel_event.is_set() else JobStatus.COMPLETED
            )
            await queries.update_scrape_job_status(
                db,
                job_id,
                status,
                messages_found=total_found,
                messages_stored=total_stored,
            )
            await db.commit()

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
                await queries.update_scrape_job_status(
                    db,
                    job_id,
                    JobStatus.FAILED,
                    error_message=str(e),
                    messages_found=total_found,
                    messages_stored=total_stored,
                )
                await db.commit()
            except Exception:
                logger.exception("Failed to update job %s status to FAILED", job_id)
            if notify_channel is not None:
                try:
                    await notify_channel.send(f"Scrape `{job_id}` failed: {e}")
                except Exception:
                    pass

        finally:
            self._tasks.pop(job_id, None)
            self._cancel_events.pop(job_id, None)

    async def _download_media(
        self,
        db: aiosqlite.Connection,
        message_id: int,
        guild_id: int,
        attachment: discord.Attachment,
    ) -> None:
        """Download a media attachment to local storage."""
        local_dir = Path(f"data/media/{guild_id}/{message_id}")
        await asyncio.to_thread(local_dir.mkdir, parents=True, exist_ok=True)
        safe_name = Path(attachment.filename).name  # Strip any directory components
        local_path = local_dir / safe_name

        try:
            await attachment.save(local_path)
        except discord.HTTPException as e:
            logger.warning("Failed to download media %s: %s", attachment.url, e)
            return

        try:
            await queries.save_downloaded_media(
                db,
                message_id=message_id,
                guild_id=guild_id,
                original_url=attachment.url,
                local_path=str(local_path),
                content_type=attachment.content_type,
                file_size=attachment.size,
            )
        except aiosqlite.Error as e:
            logger.warning("Failed to save media record for %s: %s", attachment.url, e)

    async def _job_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
        status_filter: JobStatus | None = None,
    ) -> list[app_commands.Choice[str]]:
        """Shared autocomplete for scrape job selectors."""
        if interaction.guild_id is None:
            return []
        try:
            db = self.bot.db_manager.get_connection()
            jobs = await queries.get_recent_scrape_jobs(
                db, interaction.guild_id, status_filter=status_filter
            )
        except Exception:
            logger.exception("Job autocomplete failed")
            return []
        return [
            app_commands.Choice(
                name=f"{j.job_id} -- {j.status} ({j.messages_found:,} found)",
                value=j.job_id,
            )
            for j in jobs
            if current.lower() in j.job_id.lower()
        ][:25]

    async def _status_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._job_autocomplete(interaction, current)

    async def _cancel_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._job_autocomplete(
            interaction, current, status_filter=JobStatus.RUNNING
        )

    @scrape.command(name="status", description="Check scrape job status")
    @app_commands.describe(job_id="Job ID to check")
    @app_commands.autocomplete(job_id=_status_autocomplete)
    async def scrape_status(
        self, interaction: discord.Interaction, job_id: str
    ) -> None:
        db = self.bot.db_manager.get_connection()
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
        cancel_event = self._cancel_events.get(job_id)
        if cancel_event is None:
            await interaction.response.send_message(
                f"Job `{job_id}` is not running.", ephemeral=True
            )
            return

        cancel_event.set()
        await interaction.response.send_message(
            f"Cancellation requested for job `{job_id}`.", ephemeral=True
        )


async def setup(bot: PersonaBot) -> None:
    cog = ScrapeCog(bot)
    bot.pb.add_command(cog.scrape)
    await bot.add_cog(cog)
