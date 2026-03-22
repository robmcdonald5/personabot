"""Export cog — /pb export commands for corpus generation."""

import asyncio
import json
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from personabot.bot.client import PersonaBot
from personabot.db import queries
from personabot.pipeline.export import (
    build_user_corpus,
    enforce_token_budget,
    export_jsonl,
)
from personabot.pipeline.scoring import score_and_rank
from personabot.pipeline.windowing import create_windows, inject_reply_context

logger = logging.getLogger(__name__)


def _export_path(guild_id: int, user_id: int) -> Path:
    """Canonical export file path for a user in a guild."""
    return Path(f"data/exports/{guild_id}/{user_id}/corpus.jsonl")


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

    @export.command(name="generate", description="Export a user's corpus as JSONL")
    @app_commands.describe(
        user="The user to export a corpus for",
        budget="Token budget (default: 100000)",
    )
    async def export_generate(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        budget: int | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        await interaction.response.send_message(
            f"Exporting corpus for {user.mention}...", ephemeral=True
        )

        db = self.bot.db_manager.get_connection()
        token_budget = budget or self.bot.settings.token_budget

        # 1. Fetch messages
        messages = await queries.get_user_messages(db, guild.id, user.id)
        if not messages:
            await interaction.followup.send(
                f"No messages found for {user.mention}. Run a scrape first.",
                ephemeral=True,
            )
            return

        # 2. Score and rank (CPU-intensive — run in thread)
        settings = self.bot.settings
        scored = await asyncio.to_thread(
            score_and_rank, messages, settings.top_n_messages
        )
        del messages  # Free ~50-60 MB for large message sets

        if not scored:
            await interaction.followup.send(
                f"No quality messages found for {user.mention} after filtering.",
                ephemeral=True,
            )
            return

        # 3. Create conversation windows
        windows = create_windows(
            scored,
            gap_hours=settings.window_gap_hours,
            target_author_id=user.id,
        )

        # 4. Inject reply context via targeted DB lookup (not full message dict)
        reply_ids = {
            wm.reply_to_id
            for w in windows
            for wm in w.messages
            if wm.reply_to_id is not None
        }
        reply_context = await queries.get_messages_by_ids(db, reply_ids)
        inject_reply_context(windows, reply_context)

        # 5. Token budget and corpus assembly
        selected = enforce_token_budget(windows, total_budget=token_budget)
        corpus = build_user_corpus(
            user_id=user.id,
            user_name=user.display_name,
            guild_id=guild.id,
            guild_name=guild.name,
            windows=selected,
        )

        # 7. Export JSONL (blocking I/O — run in thread)
        export_file = await asyncio.to_thread(
            export_jsonl, corpus, str(_export_path(guild.id, user.id))
        )

        # 8. Build result embed
        embed = discord.Embed(
            title=f"Corpus Export: {user.display_name}",
            color=discord.Color.teal(),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
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

        # 9. Send as Discord attachment (or fallback if too large)
        file_size = export_file.stat().st_size
        max_attachment = 25 * 1024 * 1024  # 25 MB

        if file_size <= max_attachment:
            discord_file = discord.File(
                export_file, filename=f"{user.display_name}_corpus.jsonl"
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

        export_path = _export_path(guild.id, user.id)
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

        db = self.bot.db_manager.get_connection()
        top_users = await queries.get_top_users(db, guild.id, 25)

        if not top_users:
            await interaction.response.send_message(
                "No messages scraped yet.", ephemeral=True
            )
            return

        lines = []
        for u in top_users:
            ep = _export_path(guild.id, u.author_id)
            status = "exported" if ep.exists() else "not exported"
            lines.append(f"**{u.author_name}** -- {u.message_count:,} msgs ({status})")

        embed = discord.Embed(
            title="Scraped Users",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: PersonaBot) -> None:
    cog = ExportCog(bot)
    bot.pb.add_command(cog.export)
    await bot.add_cog(cog)
