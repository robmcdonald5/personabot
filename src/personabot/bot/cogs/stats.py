"""Stats cog — /pb stats commands for analytics."""

import discord
from discord import app_commands
from discord.ext import commands

from personabot.bot.client import PersonaBot
from personabot.db import queries


class StatsCog(commands.Cog):
    """Server and user analytics."""

    def __init__(self, bot: PersonaBot) -> None:
        self.bot = bot

    stats = app_commands.Group(
        name="stats",
        description="Analytics commands",
        parent=None,
        guild_only=True,
    )

    @stats.command(name="user", description="Show message stats for a user")
    @app_commands.describe(user="The user to show stats for")
    async def stats_user(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        async with self.bot.db_conn() as db:
            user_stats = await queries.get_user_stats(db, guild.id, user.id)

        if user_stats is None:
            await interaction.response.send_message(
                f"No data found for {user.mention}. Run a scrape first.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Stats: {user_stats.author_name}",
            color=discord.Color.blue(),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(
            name="Messages", value=f"{user_stats.message_count:,}", inline=True
        )
        embed.add_field(
            name="Avg Words/Msg",
            value=f"{user_stats.avg_word_count:.1f}",
            inline=True,
        )
        embed.add_field(
            name="Total Reactions",
            value=f"{user_stats.total_reactions:,}",
            inline=True,
        )
        embed.add_field(
            name="First Message", value=user_stats.first_message[:10], inline=True
        )
        embed.add_field(
            name="Last Message", value=user_stats.last_message[:10], inline=True
        )

        await interaction.response.send_message(embed=embed)

    @stats.command(name="server", description="Show overall server stats")
    async def stats_server(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        async with self.bot.db_conn() as db:
            server_stats = await queries.get_server_stats(db, guild.id)

        embed = discord.Embed(
            title=f"Server Stats: {guild.name}",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Total Messages",
            value=f"{server_stats.total_messages:,}",
            inline=True,
        )
        embed.add_field(
            name="Unique Users",
            value=f"{server_stats.unique_users:,}",
            inline=True,
        )
        embed.add_field(
            name="Channels Scraped",
            value=f"{server_stats.total_channels:,}",
            inline=True,
        )
        embed.add_field(
            name="Total Reactions",
            value=f"{server_stats.total_reactions:,}",
            inline=True,
        )
        embed.add_field(
            name="Scrape Jobs",
            value=f"{server_stats.total_scrape_jobs:,}",
            inline=True,
        )

        await interaction.response.send_message(embed=embed)

    @stats.command(name="top", description="Show top users by message count")
    @app_commands.describe(n="Number of users to show (default: 10)")
    async def stats_top(self, interaction: discord.Interaction, n: int = 10) -> None:
        guild = interaction.guild
        assert guild is not None  # Guaranteed by guild_only

        n = min(max(n, 1), 25)
        async with self.bot.db_conn() as db:
            top_users = await queries.get_top_users(db, guild.id, n)

        if not top_users:
            await interaction.response.send_message(
                "No messages scraped yet.", ephemeral=True
            )
            return

        lines = [
            f"**{i}.** {u.author_name} -- "
            f"{u.message_count:,} msgs, "
            f"{u.total_reactions:,} reactions"
            for i, u in enumerate(top_users, 1)
        ]

        embed = discord.Embed(
            title=f"Top {len(top_users)} Users",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: PersonaBot) -> None:
    await bot.add_cog(StatsCog(bot))
