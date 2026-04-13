"""Scrape cog — /pb scrape commands for message collection."""

import asyncio
import logging
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from personabot.bot.client import PersonaBot
from personabot.bot.views import (
    ChannelPickerView,
    ScrapeWindowModal,
    UserPickerView,
    date_to_utc_midnight,
)
from personabot.db import queries
from personabot.schemas.discord import DiscordMessage, JobStatus

logger = logging.getLogger(__name__)


class ScrapeCog(commands.Cog):
    """Message scraping and collection."""

    def __init__(self, bot: PersonaBot) -> None:
        self.bot = bot
        # Task refs are held to prevent asyncio from garbage-collecting them
        # mid-run. They're cancelled cooperatively via _cancel_events on
        # cog_unload so a reload doesn't orphan an in-flight scrape.
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    async def cog_unload(self) -> None:
        """Signal cancellation and drain in-flight scrapes on reload/shutdown."""
        for event in self._cancel_events.values():
            event.set()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._cancel_events.clear()

    scrape = app_commands.Group(
        name="scrape",
        description="Message scraping commands",
        parent=None,
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @scrape.command(name="start", description="Start a message scrape job")
    @app_commands.describe(
        user=(
            "Target a specific user. Overrides the configured include list "
            "for this job."
        ),
        channel="Scrape only this channel (optional — uses configured channels)",
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

        db = self.bot.db
        cfg = await queries.get_guild_config(db, guild.id)

        if channel:
            channel_ids = [channel.id]
        elif cfg and cfg.scrape_channels:
            channel_ids = cfg.scrape_channels
        else:
            # No channels configured — offer an inline picker instead of a
            # plain-text error. User must re-run the command after saving.
            async def save_channels(ids: list[int]) -> None:
                await queries.upsert_guild_config(
                    db,
                    guild_id=guild.id,
                    guild_name=guild.name,
                    scrape_channels=ids,
                )
                await db.commit()

            channel_picker = ChannelPickerView(
                author_id=interaction.user.id, on_confirm=save_channels
            )
            await interaction.response.send_message(
                "No channels configured. Select channels to scrape below, "
                "then re-run `/pb scrape start`:",
                view=channel_picker,
                ephemeral=True,
            )
            channel_picker.message = await interaction.original_response()
            return

        # The include list is required. A per-invocation `user:` param is an
        # explicit override and bypasses this gate (otherwise `user:@alice`
        # would silently return zero messages when alice is not in the list).
        if user is None and not (cfg and cfg.included_users):

            async def save_users(ids: list[int]) -> None:
                await queries.upsert_guild_config(
                    db,
                    guild_id=guild.id,
                    guild_name=guild.name,
                    included_users=ids,
                )
                await db.commit()

            user_picker = UserPickerView(
                author_id=interaction.user.id, on_confirm=save_users
            )
            await interaction.response.send_message(
                "No users configured for scraping. Select at least one user "
                "to include, then re-run `/pb scrape start`:",
                view=user_picker,
                ephemeral=True,
            )
            user_picker.message = await interaction.original_response()
            return

        resolved_limit = (
            limit
            if limit is not None
            else (cfg.default_limit if cfg else self.bot.settings.default_limit)
        )
        # Allowed user IDs for this scrape. `user:` param forces a single-user
        # set; otherwise the gate above guarantees cfg.included_users is
        # non-empty, so this set is always non-empty by the time _run_scrape
        # reads it. Unifies both filter paths into one membership check.
        if user is not None:
            allowed_ids: set[int] = {user.id}
        else:
            assert cfg and cfg.included_users  # enforced by gate above
            allowed_ids = set(cfg.included_users)

        # The modal's `required=True` TextInputs structurally enforce the
        # "a window must be set" rule, so there is no separate error path
        # for an unconfigured window.
        async def launch_job(
            modal_inter: discord.Interaction, start_d: date, end_d: date
        ) -> None:
            start_dt = date_to_utc_midnight(start_d)
            end_dt = date_to_utc_midnight(end_d + timedelta(days=1))

            job_id = secrets.token_hex(6)
            # Only auto-create the FK parent row when no config exists yet —
            # avoids a 2-query no-op upsert on every scrape in a configured
            # guild. Needed for case: `cfg is None` + explicit `channel` arg.
            if cfg is None:
                await queries.upsert_guild_config(db, guild.id, guild.name)
            await queries.create_scrape_job(
                db,
                job_id=job_id,
                guild_id=guild.id,
                target_user_id=user.id if user else None,
                channels=channel_ids,
            )
            await db.commit()

            target_str = user.mention if user else f"{len(allowed_ids)} included users"
            await modal_inter.response.send_message(
                f"Scrape job `{job_id}` started. "
                f"Target: {target_str}, "
                f"Channels: {len(channel_ids)}, "
                f"Window: `{start_d.isoformat()}` → `{end_d.isoformat()}`, "
                f"Limit: {resolved_limit:,}",
                ephemeral=True,
            )

            cancel_event = asyncio.Event()
            self._cancel_events[job_id] = cancel_event
            task = asyncio.create_task(
                self._run_scrape(
                    job_id=job_id,
                    guild=guild,
                    channel_ids=channel_ids,
                    allowed_ids=allowed_ids,
                    limit=resolved_limit,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    notify_channel=modal_inter.channel,  # type: ignore[arg-type]
                    cancel_event=cancel_event,
                )
            )
            self._tasks[job_id] = task

        modal = ScrapeWindowModal(
            on_submit_callback=launch_job,
            default_start=cfg.scrape_start_date if cfg else None,
            default_end=cfg.scrape_end_date if cfg else None,
        )
        await interaction.response.send_modal(modal)

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
        """
        db = self.bot.db
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

                    # Download media for messages meeting reaction threshold.
                    # Images for a single message download concurrently; the
                    # scrape loop still awaits completion so per-message
                    # ordering and media-row commits stay consistent with the
                    # message upsert batch below.
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
                                    self._download_media(db, message.id, guild.id, att)
                                    for att in images
                                )
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
            jobs = await queries.get_recent_scrape_jobs(
                self.bot.db, interaction.guild_id, status_filter=status_filter
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

    # discord.py validates autocomplete callbacks have exactly 2–3 params
    # (self, interaction, current). _job_autocomplete has a 4th param
    # (status_filter), so these thin wrappers are required — not dead code.
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
        db = self.bot.db
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
    await bot.add_cog(ScrapeCog(bot))
