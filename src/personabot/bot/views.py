"""Reusable Discord UI Views for PersonaBot commands."""

from collections.abc import Awaitable, Callable

import discord


class _PickerViewBase(discord.ui.View):
    """Base class for multi-select picker views with confirmation."""

    def __init__(
        self,
        author_id: int,
        on_confirm: Callable[[list[int]], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=180)
        self.author_id = author_id
        self.on_confirm = on_confirm
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This is not your configuration dialog.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(content="Selection timed out.", view=self)
            except discord.HTTPException:
                pass


class ChannelPickerView(_PickerViewBase):
    """Multi-select channel picker with confirmation button."""

    def __init__(
        self,
        author_id: int,
        on_confirm: Callable[[list[int]], Awaitable[None]],
    ) -> None:
        super().__init__(author_id=author_id, on_confirm=on_confirm)
        self.selected_channels: list[discord.app_commands.AppCommandChannel] = []

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Select channels to scrape...",
        min_values=1,
        max_values=25,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        row=0,
    )
    async def channel_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.ChannelSelect,
    ) -> None:
        self.selected_channels = list(select.values)  # type: ignore[arg-type]
        names = ", ".join(f"#{c.name}" for c in self.selected_channels)
        await interaction.response.edit_message(
            content=f"Selected: **{names}**\nClick **Confirm** to save.",
            view=self,
        )

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, row=1)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not self.selected_channels:
            await interaction.response.send_message(
                "Select at least one channel first.", ephemeral=True
            )
            return

        channel_ids = [c.id for c in self.selected_channels]
        await self.on_confirm(channel_ids)
        names = ", ".join(f"<#{cid}>" for cid in channel_ids)
        self.stop()
        await interaction.response.edit_message(
            content=f"Scrape channels set to: {names}", view=None
        )


class UserPickerView(_PickerViewBase):
    """Multi-select user picker with confirmation button."""

    def __init__(
        self,
        author_id: int,
        on_confirm: Callable[[list[int]], Awaitable[None]],
    ) -> None:
        super().__init__(author_id=author_id, on_confirm=on_confirm)
        self.selected_users: list[discord.Member | discord.User] = []

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Select users to exclude...",
        min_values=1,
        max_values=25,
        row=0,
    )
    async def user_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.UserSelect,
    ) -> None:
        self.selected_users = list(select.values)
        names = ", ".join(u.display_name for u in self.selected_users)
        await interaction.response.edit_message(
            content=f"Selected: **{names}**\nClick **Confirm** to save.",
            view=self,
        )

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, row=1)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not self.selected_users:
            await interaction.response.send_message(
                "Select at least one user first.", ephemeral=True
            )
            return

        user_ids = [u.id for u in self.selected_users]
        await self.on_confirm(user_ids)
        names = ", ".join(f"<@{uid}>" for uid in user_ids)
        self.stop()
        await interaction.response.edit_message(
            content=f"Excluded users set to: {names}", view=None
        )
