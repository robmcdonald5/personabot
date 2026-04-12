"""Configuration cog — /pb config commands."""

import logging
from datetime import date
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from personabot.bot.client import PersonaBot
from personabot.bot.views import (
    ChannelPickerView,
    ResetConfirmView,
    ScrapeWindowModal,
    UserPickerView,
)
from personabot.db import queries

logger = logging.getLogger(__name__)


class ConfigCog(commands.Cog):
    """Server configuration management."""

    def __init__(self, bot: PersonaBot) -> None:
        self.bot = bot

    config = app_commands.Group(
        name="config",
        description="Server configuration",
        parent=None,
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def _save_guild_fields(self, guild: discord.Guild, **fields: Any) -> None:
        """Upsert one or more guild_config fields and commit.

        Collapses the identical save-closure pattern that would otherwise
        repeat inside every picker/modal callback in this cog (and the
        scrape cog's channel-picker recovery path).
        """
        db = self.bot.db
        await queries.upsert_guild_config(
            db, guild_id=guild.id, guild_name=guild.name, **fields
        )
        await db.commit()

    @config.command(name="show", description="Display current server configuration")
    async def config_show(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None  # Guaranteed by guild_only
        db = self.bot.db

        cfg = await queries.get_guild_config(db, interaction.guild.id)
        if cfg is None:
            await interaction.response.send_message(
                "No configuration found. Use `/pb config set-channels` to get started.",
                ephemeral=True,
            )
            return

        channels_str = ", ".join(f"<#{c}>" for c in cfg.scrape_channels) or "None"
        included_str = ", ".join(f"<@{u}>" for u in cfg.included_users) or (
            "None — `/pb config set-included-users`"
        )
        window_str = (
            f"`{cfg.scrape_start_date}` → `{cfg.scrape_end_date}` (UTC)"
            if cfg.scrape_start_date and cfg.scrape_end_date
            else "Not set — `/pb config set-window`"
        )

        embed = discord.Embed(
            title="PersonaBot Configuration",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Scrape Channels", value=channels_str, inline=False)
        embed.add_field(name="Included Users", value=included_str, inline=False)
        embed.add_field(name="Scrape Window", value=window_str, inline=False)
        embed.add_field(name="Message Limit", value=str(cfg.default_limit), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config.command(
        name="set-channels",
        description="Set channels to scrape",
    )
    async def config_set_channels(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        view = ChannelPickerView(
            author_id=interaction.user.id,
            on_confirm=lambda ids: self._save_guild_fields(guild, scrape_channels=ids),
        )
        await interaction.response.send_message(
            "Select channels to scrape:", view=view, ephemeral=True
        )
        view.message = await interaction.original_response()

    @config.command(
        name="set-included-users",
        description=(
            "Required allowlist — at least one user must be set before scraping"
        ),
    )
    async def config_set_included_users(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        view = UserPickerView(
            author_id=interaction.user.id,
            on_confirm=lambda ids: self._save_guild_fields(guild, included_users=ids),
        )
        await interaction.response.send_message(
            "Select users to include in scraping:", view=view, ephemeral=True
        )
        view.message = await interaction.original_response()

    @config.command(
        name="set-window",
        description="Set the default scrape date window (start/end)",
    )
    async def config_set_window(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only
        cfg = await queries.get_guild_config(self.bot.db, guild.id)

        async def save_window(
            modal_inter: discord.Interaction, start_d: date, end_d: date
        ) -> None:
            await self._save_guild_fields(
                guild,
                scrape_start_date=start_d.isoformat(),
                scrape_end_date=end_d.isoformat(),
            )
            await modal_inter.response.send_message(
                f"Scrape window set: `{start_d.isoformat()}` → "
                f"`{end_d.isoformat()}` (UTC).",
                ephemeral=True,
            )

        modal = ScrapeWindowModal(
            on_submit_callback=save_window,
            default_start=cfg.scrape_start_date if cfg else None,
            default_end=cfg.scrape_end_date if cfg else None,
        )
        await interaction.response.send_modal(modal)

    @config.command(
        name="reset",
        description="Reset all configuration fields to defaults",
    )
    async def config_reset(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        async def run_reset() -> bool:
            db = self.bot.db
            applied = await queries.reset_guild_config(db, guild.id)
            await db.commit()
            return applied

        view = ResetConfirmView(author_id=interaction.user.id, on_reset=run_reset)
        await interaction.response.send_message(
            "This will clear channels, included users, scrape window, "
            "and message limit.\n"
            "**This cannot be undone. Are you sure?**",
            view=view,
            ephemeral=True,
        )
        view.message = await interaction.original_response()

    @config.command(
        name="set-message-limit",
        description="Set the default message limit per scrape",
    )
    @app_commands.describe(limit="Maximum messages to scrape (default: 10000)")
    async def config_set_message_limit(
        self, interaction: discord.Interaction, limit: int
    ) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        if limit < 100 or limit > 100000:
            await interaction.response.send_message(
                "Limit must be between 100 and 100,000.", ephemeral=True
            )
            return

        await self._save_guild_fields(guild, default_limit=limit)
        await interaction.response.send_message(
            f"Default message limit set to {limit:,}.", ephemeral=True
        )


async def setup(bot: PersonaBot) -> None:
    await bot.add_cog(ConfigCog(bot))
