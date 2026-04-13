"""Reusable Discord UI Views for PersonaBot commands."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone

import discord


def parse_date_ymd(raw: str, field_name: str) -> date:
    """Parse a YYYY-MM-DD (or unpadded YYYY-M-D) string into a `date`.

    `strptime("%Y-%m-%d")` natively accepts both padded and unpadded
    month/day, rejects 2-digit years, wrong separators, trailing time
    components, and invalid calendar dates (Feb 30, month 13, etc.) —
    so one handler covers all failure modes.
    """
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(
            f"{field_name} must be a valid date in YYYY-MM-DD format. Got: `{raw}`"
        ) from e


def date_to_utc_midnight(d: date) -> datetime:
    """Convert a bare date to a timezone-aware UTC midnight datetime.

    Used to build `channel.history(after=..., before=...)` bounds from
    user-entered YYYY-MM-DD values. The end bound should be the midnight
    of the day AFTER the user's end date so the whole day is included,
    since `before` is an exclusive boundary.
    """
    return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)


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
    """Multi-select user picker for building an included-users allowlist."""

    def __init__(
        self,
        author_id: int,
        on_confirm: Callable[[list[int]], Awaitable[None]],
    ) -> None:
        super().__init__(author_id=author_id, on_confirm=on_confirm)
        self.selected_users: list[discord.Member | discord.User] = []

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Select users to include...",
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
            content=f"Included users set to: {names}", view=None
        )


class ScrapeWindowModal(discord.ui.Modal, title="Scrape Window"):
    """Modal that collects an absolute start/end date range for a scrape.

    Used by both `/pb config set-window` (persist as guild default) and
    `/pb scrape start` (per-job override, pre-filled with the guild default
    if one exists). Both date fields are required, which structurally enforces
    the "a time period must be set" rule with no separate error path.
    """

    start_date: discord.ui.TextInput = discord.ui.TextInput(
        label="Start Date",
        placeholder="YYYY-MM-DD",
        min_length=8,
        max_length=10,
        required=True,
    )
    end_date: discord.ui.TextInput = discord.ui.TextInput(
        label="End Date",
        placeholder="YYYY-MM-DD",
        min_length=8,
        max_length=10,
        required=True,
    )

    def __init__(
        self,
        *,
        on_submit_callback: Callable[
            [discord.Interaction, date, date], Awaitable[None]
        ],
        default_start: str | None = None,
        default_end: str | None = None,
    ) -> None:
        super().__init__()
        self._cb = on_submit_callback
        if default_start:
            self.start_date.default = default_start
        if default_end:
            self.end_date.default = default_end

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            start_d = parse_date_ymd(self.start_date.value, "Start Date")
            end_d = parse_date_ymd(self.end_date.value, "End Date")
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        if start_d > end_d:
            await interaction.response.send_message(
                "Start date must be on or before end date.", ephemeral=True
            )
            return

        today = datetime.now(timezone.utc).date()
        if end_d > today:
            await interaction.response.send_message(
                "End date cannot be in the future.", ephemeral=True
            )
            return

        await self._cb(interaction, start_d, end_d)


class ResetConfirmView(discord.ui.View):
    """Destructive-action confirmation view for `/pb config reset`.

    Author-only, 30s timeout. The confirm button runs `on_reset` (which
    performs the reset and returns True if a row was actually updated) and
    edits the message in a single call with the appropriate outcome. Mirrors
    the callback pattern used by `ChannelPickerView` / `UserPickerView`.
    """

    def __init__(
        self,
        author_id: int,
        on_reset: Callable[[], Awaitable[bool]],
    ) -> None:
        super().__init__(timeout=30.0)
        self.author_id = author_id
        self.on_reset = on_reset
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This is not your reset dialog.", ephemeral=True
            )
            return False
        return True

    def _disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    async def on_timeout(self) -> None:
        self._disable_all()
        if self.message is not None:
            try:
                await self.message.edit(
                    content="Reset confirmation timed out.", view=self
                )
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Reset Config", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._disable_all()
        reset_applied = await self.on_reset()
        msg = (
            "Configuration has been reset to defaults."
            if reset_applied
            else "No configuration to reset."
        )
        self.stop()
        await interaction.response.edit_message(content=msg, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(content="Reset cancelled.", view=None)
