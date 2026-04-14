"""Configuration cog — /pb config commands."""

from collections.abc import Awaitable, Callable
from datetime import date

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

_AdvanceCallback = Callable[[discord.Interaction], Awaitable[None]]


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

    # ------------------------------------------------------------------
    # Prompt helpers — shared by the individual set-* commands and the
    # `/pb config setup` chained flow. Each sends a picker/modal and
    # wires its confirm to save + optionally call ``on_advance`` to
    # chain into the next setup step.
    # ------------------------------------------------------------------

    async def _prompt_channels(
        self,
        interaction: discord.Interaction,
        *,
        prompt: str = "Select channels to scrape:",
        via_followup: bool = False,
        on_advance: _AdvanceCallback | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        async def after(button_inter: discord.Interaction, ids: list[int]) -> None:
            await self.bot.save_guild_fields(guild, scrape_channels=ids)
            if on_advance is not None:
                await on_advance(button_inter)

        view = ChannelPickerView(author_id=interaction.user.id, on_confirm=after)
        await self._send_picker(interaction, view, prompt, via_followup)

    async def _prompt_users(
        self,
        interaction: discord.Interaction,
        *,
        prompt: str = "Select users to include in scraping:",
        via_followup: bool = False,
        on_advance: _AdvanceCallback | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None

        async def after(button_inter: discord.Interaction, ids: list[int]) -> None:
            await self.bot.save_guild_fields(guild, included_users=ids)
            if on_advance is not None:
                await on_advance(button_inter)

        view = UserPickerView(author_id=interaction.user.id, on_confirm=after)
        await self._send_picker(interaction, view, prompt, via_followup)

    @staticmethod
    async def _send_picker(
        interaction: discord.Interaction,
        view: ChannelPickerView | UserPickerView,
        prompt: str,
        via_followup: bool,
    ) -> None:
        if via_followup:
            view.message = await interaction.followup.send(
                prompt, view=view, ephemeral=True, wait=True
            )
        else:
            await interaction.response.send_message(prompt, view=view, ephemeral=True)
            view.message = await interaction.original_response()

    async def _open_window_modal(
        self,
        interaction: discord.Interaction,
        *,
        default_start: str | None = None,
        default_end: str | None = None,
    ) -> None:
        """Open the scrape-window modal on ``interaction`` (must be unresponded)."""
        guild = interaction.guild
        assert guild is not None

        async def save_window(
            modal_inter: discord.Interaction, start_d: date, end_d: date
        ) -> None:
            await self.bot.save_guild_fields(
                guild,
                scrape_start_date=start_d.isoformat(),
                scrape_end_date=end_d.isoformat(),
            )
            await modal_inter.followup.send(
                f"Scrape window set: `{start_d.isoformat()}` → "
                f"`{end_d.isoformat()}` (UTC).",
                ephemeral=True,
            )

        modal = ScrapeWindowModal(
            on_submit_callback=save_window,
            default_start=default_start,
            default_end=default_end,
        )
        await interaction.response.send_modal(modal)

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    @config.command(name="show", description="Display current server configuration")
    async def config_show(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None  # Guaranteed by guild_only

        async with self.bot.db_conn() as db:
            cfg = await queries.get_guild_config(db, interaction.guild.id)
        if cfg is None:
            await interaction.response.send_message(
                "No configuration found. Run `/pb config setup` to "
                "configure everything sequentially.",
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
        name="setup",
        description="Configure channels → included users → window sequentially",
    )
    async def config_setup(self, interaction: discord.Interaction) -> None:
        """Chain the channels / users / window prompts using the same UI
        primitives as the individual set-* commands.

        The window step goes through an intermediate "Set window" button
        because Discord's modal API requires an unresponded interaction,
        and by the time the users picker finishes we are past that point.
        The button click is a fresh interaction the modal can use.
        """
        assert interaction.guild is not None

        async def after_users_saved(users_inter: discord.Interaction) -> None:
            async def open_modal(btn_inter: discord.Interaction) -> None:
                await self._open_window_modal(btn_inter)

            window_prompt = _NextStepButtonView(
                author_id=interaction.user.id,
                label="Set window",
                on_click=open_modal,
            )
            window_prompt.message = await users_inter.followup.send(
                "Step 3 of 3: click the button below to set the scrape window.",
                view=window_prompt,
                ephemeral=True,
                wait=True,
            )

        async def after_channels_saved(
            channels_inter: discord.Interaction,
        ) -> None:
            await self._prompt_users(
                channels_inter,
                prompt="Step 2 of 3: select users to include in scraping:",
                via_followup=True,
                on_advance=after_users_saved,
            )

        await self._prompt_channels(
            interaction,
            prompt="Step 1 of 3: select channels to scrape:",
            on_advance=after_channels_saved,
        )

    @config.command(
        name="set-channels",
        description="Set channels to scrape",
    )
    async def config_set_channels(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self._prompt_channels(interaction)

    @config.command(
        name="set-included-users",
        description=(
            "Required allowlist — at least one user must be set before scraping"
        ),
    )
    async def config_set_included_users(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self._prompt_users(interaction)

    @config.command(
        name="set-window",
        description="Set the default scrape date window (start/end)",
    )
    async def config_set_window(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        async with self.bot.db_conn() as db:
            cfg = await queries.get_guild_config(db, interaction.guild.id)
        await self._open_window_modal(
            interaction,
            default_start=cfg.scrape_start_date if cfg else None,
            default_end=cfg.scrape_end_date if cfg else None,
        )

    @config.command(
        name="reset",
        description="Reset all configuration fields to defaults",
    )
    async def config_reset(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None

        async def run_reset() -> str:
            async with self.bot.db_conn() as db, db.transaction():
                ok = await queries.reset_guild_config(db, guild.id)
            return (
                "Configuration has been reset to defaults."
                if ok
                else "No configuration to reset."
            )

        view = ResetConfirmView(
            author_id=interaction.user.id,
            on_reset=run_reset,
            button_label="Reset Config",
        )
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
        assert guild is not None

        if limit < 100 or limit > 100000:
            await interaction.response.send_message(
                "Limit must be between 100 and 100,000.", ephemeral=True
            )
            return

        await self.bot.save_guild_fields(guild, default_limit=limit)
        await interaction.response.send_message(
            f"Default message limit set to {limit:,}.", ephemeral=True
        )


class _NextStepButtonView(discord.ui.View):
    """One-button view used by ``config_setup`` to reach a fresh
    interaction for opening the scrape-window modal.

    Modals require an unresponded interaction as their initial response,
    so the setup chain can't open one directly from the users picker's
    confirm button (already consumed by ``edit_message``). Sending this
    view as a followup gives us a button whose click is a fresh
    interaction on which ``_open_window_modal`` can ``send_modal``.
    """

    def __init__(
        self,
        author_id: int,
        label: str,
        on_click: _AdvanceCallback,
    ) -> None:
        super().__init__(timeout=180)
        self.author_id = author_id
        self.on_click = on_click
        self.message: discord.Message | discord.WebhookMessage | None = None

        button: discord.ui.Button = discord.ui.Button(
            label=label, style=discord.ButtonStyle.primary
        )
        button.callback = self._on_click  # type: ignore[method-assign]
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This is not your setup dialog.", ephemeral=True
            )
            return False
        return True

    async def _on_click(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self.on_click(interaction)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(content="Setup step timed out.", view=self)
            except discord.HTTPException:
                pass


async def setup(bot: PersonaBot) -> None:
    await bot.add_cog(ConfigCog(bot))
