"""Export cog — /pb export commands for corpus generation."""

import asyncio
import json
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from personabot.bot.client import PersonaBot
from personabot.bot.cogs.scrape import fetch_job_choices
from personabot.bot.views import ExportUserPickerView
from personabot.db import queries
from personabot.pipeline.export import (
    build_user_corpus,
    enforce_token_budget,
    export_jsonl,
)
from personabot.pipeline.scoring import score_and_rank
from personabot.pipeline.windowing import create_windows, inject_reply_context


def _scan_exported_user_ids(guild_export_dir: Path) -> set[int]:
    """Return the set of user_ids that have a corpus.jsonl under a guild dir."""
    if not guild_export_dir.exists():
        return set()
    result: set[int] = set()
    for user_dir in guild_export_dir.iterdir():
        if not user_dir.is_dir():
            continue
        if (user_dir / "corpus.jsonl").exists():
            try:
                result.add(int(user_dir.name))
            except ValueError:
                continue
    return result


class ExportCog(commands.Cog):
    """Corpus export — preprocess messages and produce JSONL for OnWriting."""

    def __init__(self, bot: PersonaBot) -> None:
        self.bot = bot

    export = app_commands.Group(
        name="export",
        description="Corpus export commands",
        parent=None,
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def _job_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for job_id:. Only shows jobs with stored data."""
        return await fetch_job_choices(
            self.bot,
            interaction,
            current,
            predicate=lambda j: j.messages_stored > 0,
            label=lambda j: (
                f"{j.job_id} -- {j.status} ({j.messages_stored:,} stored)"
            ),
        )

    @export.command(name="generate", description="Export a user's corpus as JSONL")
    @app_commands.describe(
        job_id=(
            "Optional: export only messages first captured by this specific "
            "scrape (autocomplete). Omit to use every user currently in the DB."
        ),
    )
    @app_commands.autocomplete(job_id=_job_autocomplete)
    async def export_generate(
        self,
        interaction: discord.Interaction,
        job_id: str | None = None,
    ) -> None:
        """Resolve the export target then run the pipeline.

        When ``job_id`` is omitted, the command exports from every user
        with stored messages in this guild (auto-picks single user,
        shows a picker for multi-user). When ``job_id`` is provided,
        every DB read is filtered by ``messages.job_id = job_id`` so the
        result is exactly that scrape's captured corpus — per the
        first-wins upsert semantic in ``queries._UPSERT_MESSAGE_SQL``.
        """
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        if job_id is not None:
            # Authorization gate: `scrape_jobs.job_id` is a guild-blind PK
            # (`secrets.token_hex(6)`) and Discord autocomplete is just a
            # hint — a user can free-type any string. A cross-guild hit
            # must look identical to "not found" so we don't leak the
            # existence of jobs in other guilds.
            async with self.bot.db_conn() as db:
                job = await queries.get_scrape_job(db, job_id)
            if job is None or job.guild_id != guild.id:
                await interaction.response.send_message(
                    f"Job `{job_id}` not found.", ephemeral=True
                )
                return

        # n=25 matches Discord's Select max options.
        async with self.bot.db_conn() as db:
            top_users = await queries.get_top_users(db, guild.id, n=25, job_id=job_id)
        if not top_users:
            if job_id is not None:
                await self._respond_empty_job(interaction, job_id)
            else:
                await self._respond_empty_db(interaction, guild.id)
            return

        if len(top_users) == 1:
            tu = top_users[0]
            member = guild.get_member(tu.author_id)
            await interaction.response.defer(ephemeral=True)
            await self._do_export(
                interaction,
                tu.author_id,
                member.display_name if member else tu.author_name,
                member.display_avatar.url if member else None,
                job_id=job_id,
            )
            return

        async def on_pick(picker_inter: discord.Interaction, picked_id: int) -> None:
            await picker_inter.response.defer(ephemeral=True)
            picked = next(tu for tu in top_users if tu.author_id == picked_id)
            picked_member = guild.get_member(picked_id)
            await self._do_export(
                picker_inter,
                picked_id,
                picked_member.display_name if picked_member else picked.author_name,
                picked_member.display_avatar.url if picked_member else None,
                job_id=job_id,
            )

        picker = ExportUserPickerView(
            author_id=interaction.user.id,
            choices=[
                (tu.author_id, tu.author_name, tu.message_count) for tu in top_users
            ],
            on_pick=on_pick,
        )
        if job_id is not None:
            prompt = (
                f"Job `{job_id}` captured messages from {len(top_users)} "
                "users. Pick one to export:"
            )
        else:
            prompt = (
                f"The database has messages from {len(top_users)} users. "
                "Pick one to export:"
            )
        await interaction.response.send_message(prompt, view=picker, ephemeral=True)
        picker.message = await interaction.original_response()

    async def _respond_empty_job(
        self, interaction: discord.Interaction, job_id: str
    ) -> None:
        """Actionable error when a specific job_id has no stored messages."""
        await interaction.response.send_message(
            f"Job `{job_id}` has no stored messages in the retention window.",
            ephemeral=True,
        )

    async def _respond_empty_db(
        self, interaction: discord.Interaction, guild_id: int
    ) -> None:
        """Actionable error when the whole guild has no stored messages.

        Distinguishes "this guild has never scraped" from "retention wiped
        the last scrape's data" so the operator knows which button to push.
        """
        async with self.bot.db_conn() as db:
            job_count = await queries.count_scrape_jobs(db, guild_id)
        if job_count == 0:
            await interaction.response.send_message(
                "No scrapes have been run for this server yet. "
                "Run `/pb scrape start` first.",
                ephemeral=True,
            )
        else:
            hours = self.bot.settings.retention_hours
            await interaction.response.send_message(
                f"No messages in the database. The {hours}-hour "
                "retention window may have expired since the last "
                "scrape — run `/pb scrape start` again.",
                ephemeral=True,
            )

    async def _do_export(
        self,
        interaction: discord.Interaction,
        user_id: int,
        display_name: str,
        avatar_url: str | None,
        *,
        job_id: str | None = None,
    ) -> None:
        """Run the full export pipeline for a resolved target user.

        Caller must have already deferred the interaction — this method
        always uses ``followup.send``. When ``job_id`` is provided, the
        message fetch is filtered by job.
        """
        guild = interaction.guild
        assert guild is not None
        settings = self.bot.settings
        token_budget = settings.token_budget

        async with self.bot.db_conn() as db:
            messages = await queries.get_user_messages(
                db, guild.id, user_id, job_id=job_id
            )
        if not messages:
            await interaction.followup.send(
                f"No messages found for **{display_name}**. They weren't in "
                "the scraped data — run `/pb scrape start` after editing "
                "channels / included users / window.",
                ephemeral=True,
            )
            return

        scored = await asyncio.to_thread(
            score_and_rank, messages, settings.top_n_messages
        )
        del messages  # Drop full message cache after top-N selection

        if not scored:
            await interaction.followup.send(
                f"No quality messages found for **{display_name}** after filtering.",
                ephemeral=True,
            )
            return

        windows = create_windows(
            scored,
            gap_hours=settings.window_gap_hours,
            target_author_id=user_id,
        )

        reply_ids = {
            wm.reply_to_id
            for w in windows
            for wm in w.messages
            if wm.reply_to_id is not None
        }
        async with self.bot.db_conn() as db:
            reply_context = await queries.get_messages_by_ids(db, reply_ids)
        inject_reply_context(windows, reply_context)

        selected = enforce_token_budget(windows, total_budget=token_budget)
        corpus = build_user_corpus(
            user_id=user_id,
            user_name=display_name,
            guild_id=guild.id,
            guild_name=guild.name,
            windows=selected,
        )

        export_file = await asyncio.to_thread(
            export_jsonl,
            corpus,
            settings.export_path(guild.id, user_id),
        )

        embed = discord.Embed(
            title=f"Corpus Export: {display_name}",
            color=discord.Color.teal(),
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.add_field(
            name="Messages", value=f"{corpus.total_messages:,}", inline=True
        )
        embed.add_field(name="Windows", value=f"{corpus.total_windows:,}", inline=True)
        embed.add_field(name="Token Budget", value=f"{token_budget:,}", inline=True)
        if corpus.date_range.earliest:
            embed.add_field(
                name="Date Range",
                value=(
                    f"{corpus.date_range.earliest[:10]} to "
                    f"{corpus.date_range.latest[:10]}"
                ),
                inline=False,
            )

        hours = self.bot.settings.retention_hours
        embed.set_footer(
            text=f"Data expires in {hours} hours. Download this file to keep it."
        )

        file_size = export_file.stat().st_size
        max_attachment = 25 * 1024 * 1024  # 25 MB

        if file_size <= max_attachment:
            discord_file = discord.File(
                export_file, filename=f"{display_name}_corpus.jsonl"
            )
            await interaction.followup.send(embed=embed, file=discord_file)
        else:
            size_mb = file_size / (1024 * 1024)
            embed.add_field(
                name="Warning",
                value=(
                    f"File too large for Discord ({size_mb:.1f} MB > 25 MB). "
                    "Try reducing the token budget."
                ),
                inline=False,
            )
            await interaction.followup.send(embed=embed)

    @export.command(name="view", description="View stats for a user's latest export")
    @app_commands.describe(user="The user whose export to inspect")
    async def export_view(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        export_path = self.bot.settings.export_path(guild.id, user.id)
        if not export_path.exists():
            await interaction.response.send_message(
                f"No export found for {user.mention}. "
                "Run `/pb export generate` first.",
                ephemeral=True,
            )
            return

        # Read export metadata (blocking I/O — run in thread)
        def _read_stats() -> tuple[dict, int]:
            with open(export_path, encoding="utf-8") as f:
                meta = json.loads(f.readline())
            return meta, export_path.stat().st_size

        metadata, file_size = await asyncio.to_thread(_read_stats)
        size_str = (
            f"{file_size / 1024:.1f} KB"
            if file_size < 1024 * 1024
            else f"{file_size / (1024 * 1024):.1f} MB"
        )

        total_msgs = metadata.get("total_messages", 0)
        total_wins = metadata.get("total_windows", 0)

        embed = discord.Embed(
            title=f"Export: {user.display_name}",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Messages",
            value=f"{total_msgs:,}",
            inline=True,
        )
        embed.add_field(name="Windows", value=f"{total_wins:,}", inline=True)
        embed.add_field(name="File Size", value=size_str, inline=True)
        date_range = metadata.get("date_range", {})
        if date_range.get("earliest"):
            embed.add_field(
                name="Date Range",
                value=f"{date_range['earliest'][:10]} to {date_range['latest'][:10]}",
                inline=False,
            )
        embed.add_field(name="Path", value=f"`{export_path}`", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @export.command(name="list", description="List users with scraped messages")
    async def export_list(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        async with self.bot.db_conn() as db:
            top_users = await queries.get_top_users(db, guild.id, 25)

        if not top_users:
            await interaction.response.send_message(
                "No messages scraped yet.", ephemeral=True
            )
            return

        # Single directory scan beats N per-user Path.exists() calls on
        # the event loop thread. Guild export dirs look like
        # {exports_dir}/{guild_id}/{user_id}/corpus.jsonl.
        guild_export_dir = self.bot.settings.exports_dir / str(guild.id)
        exported_user_ids = await asyncio.to_thread(
            _scan_exported_user_ids, guild_export_dir
        )

        lines = []
        for u in top_users:
            status = "exported" if u.author_id in exported_user_ids else "not exported"
            lines.append(f"**{u.author_name}** -- {u.message_count:,} msgs ({status})")

        embed = discord.Embed(
            title="Scraped Users",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: PersonaBot) -> None:
    await bot.add_cog(ExportCog(bot))
