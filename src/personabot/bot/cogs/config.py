"""Configuration cog — /pb config commands."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from personabot.bot.client import PersonaBot
from personabot.bot.views import ChannelPickerView, UserPickerView
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

    @config.command(name="show", description="Display current server configuration")
    async def config_show(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None  # Guaranteed by guild_only
        db = self.bot.db_manager.get_connection()

        cfg = await queries.get_guild_config(db, interaction.guild.id)
        if cfg is None:
            await interaction.response.send_message(
                "No configuration found. Use `/pb config set-channels` to get started.",
                ephemeral=True,
            )
            return

        channels_str = ", ".join(f"<#{c}>" for c in cfg.scrape_channels) or "None"
        excluded_str = ", ".join(f"<@{u}>" for u in cfg.excluded_users) or "None"

        embed = discord.Embed(
            title="PersonaBot Configuration",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Scrape Channels", value=channels_str, inline=False)
        embed.add_field(name="Excluded Users", value=excluded_str, inline=False)
        embed.add_field(name="Message Limit", value=str(cfg.default_limit), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config.command(
        name="set-channels",
        description="Set channels to scrape",
    )
    async def config_set_channels(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        async def save_channels(channel_ids: list[int]) -> None:
            db = self.bot.db_manager.get_connection()
            await queries.upsert_guild_config(
                db,
                guild_id=guild.id,
                guild_name=guild.name,
                scrape_channels=channel_ids,
            )
            await db.commit()

        view = ChannelPickerView(
            author_id=interaction.user.id, on_confirm=save_channels
        )
        await interaction.response.send_message(
            "Select channels to scrape:", view=view, ephemeral=True
        )
        view.message = await interaction.original_response()

    @config.command(
        name="set-excluded-users",
        description="Set users to exclude from scraping",
    )
    async def config_set_excluded_users(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        async def save_users(user_ids: list[int]) -> None:
            db = self.bot.db_manager.get_connection()
            await queries.upsert_guild_config(
                db,
                guild_id=guild.id,
                guild_name=guild.name,
                excluded_users=user_ids,
            )
            await db.commit()

        view = UserPickerView(author_id=interaction.user.id, on_confirm=save_users)
        await interaction.response.send_message(
            "Select users to exclude from scraping:", view=view, ephemeral=True
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

        db = self.bot.db_manager.get_connection()
        await queries.upsert_guild_config(
            db,
            guild_id=guild.id,
            guild_name=guild.name,
            default_limit=limit,
        )
        await db.commit()
        await interaction.response.send_message(
            f"Default message limit set to {limit:,}.", ephemeral=True
        )


async def setup(bot: PersonaBot) -> None:
    await bot.add_cog(ConfigCog(bot))
