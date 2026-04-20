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
        on_confirm: Callable[[discord.Interaction, list[int]], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=180)
        self.author_id = author_id
        self.on_confirm = on_confirm
        # Chained-flow paths (e.g. /pb config setup) deliver the view
        # via ``followup.send(..., wait=True)`` which returns a
        # ``WebhookMessage``. Manual paths use ``interaction.original_response()``
        # which returns a ``Message``. Both support ``.edit(...)`` for
        # the on_timeout path.
        self.message: discord.Message | discord.WebhookMessage | None = None

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
        on_confirm: Callable[[discord.Interaction, list[int]], Awaitable[None]],
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
        names = ", ".join(f"<#{cid}>" for cid in channel_ids)
        self.stop()
        await interaction.response.edit_message(
            content=f"Scrape channels set to: {names}", view=None
        )
        await self.on_confirm(interaction, channel_ids)


class UserPickerView(_PickerViewBase):
    """Multi-select user picker for building an included-users allowlist."""

    def __init__(
        self,
        author_id: int,
        on_confirm: Callable[[discord.Interaction, list[int]], Awaitable[None]],
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
        names = ", ".join(f"<@{uid}>" for uid in user_ids)
        self.stop()
        await interaction.response.edit_message(
            content=f"Included users set to: {names}", view=None
        )
        await self.on_confirm(interaction, user_ids)


class ScrapeWindowModal(discord.ui.Modal, title="Scrape Window"):
    """Modal that collects an absolute start/end date range for a scrape.

    Used by `/pb config set-window` (persist as guild default) and
    `/pb config setup` (as the final step of the chained flow). Both
    date fields are required, which structurally enforces the "a time
    period must be set" rule with no separate error path.

    ``on_submit`` defers the interaction after validation and before
    calling ``on_submit_callback`` — callbacks must therefore use
    ``modal_inter.followup.send`` rather than ``response.send_message``.
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

        # Defer before calling the callback so the callback can use
        # followup.send without having to know whether it was invoked
        # via modal or via a pre-deferred button flow.
        await interaction.response.defer(ephemeral=True)
        await self._cb(interaction, start_d, end_d)


class _ExportUserSelect(discord.ui.Select):
    """Inner Select subclass for ExportUserPickerView.

    Subclassing (instead of assigning .callback externally) lets the
    callback read ``self.values`` directly — a typed ``list[str]`` —
    avoiding the untyped ``interaction.data.get("values")`` fallback
    that mypy cannot narrow. discord.py treats a Select subclass's
    ``callback`` method as the selection handler automatically.
    """

    def __init__(
        self,
        choices: list[tuple[int, str, int]],
        on_pick: Callable[[discord.Interaction, int], Awaitable[None]],
    ) -> None:
        options = [
            discord.SelectOption(
                # Label and description are capped at 100 chars each by
                # Discord — slice defensively in case an author_name is
                # a long nickname with Unicode.
                label=name[:100],
                description=f"{count:,} messages"[:100],
                value=str(aid),
            )
            for (aid, name, count) in choices[:25]
        ]
        super().__init__(
            placeholder="Choose a user to export...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._on_pick = on_pick

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.values:
            await interaction.response.send_message(
                "No selection received.", ephemeral=True
            )
            return
        picked_id = int(self.values[0])
        if self.view is not None:
            self.view.stop()
        await self._on_pick(interaction, picked_id)


class ExportUserPickerView(discord.ui.View):
    """Ephemeral picker shown by /pb export generate when user: is omitted
    AND the DB has messages from more than one author.

    Unlike the ``UserPickerView`` used in /pb config, this view's options
    are NOT the guild member list — they are exactly the users present in
    the ``messages`` table for this guild, labeled with their message
    counts. Using ``discord.ui.Select`` with string options (author_id
    stringified) rather than ``UserSelect`` lets us constrain the choices
    to users the bot actually has data for, avoiding the dead-end where
    an operator picks someone who was never scraped.

    Author-only, 120s timeout. On pick the view calls ``on_pick`` with
    the fresh select interaction and the picked author_id — the caller
    is expected to acknowledge the interaction inside ``on_pick`` (e.g.
    via ``response.defer`` + followup).
    """

    def __init__(
        self,
        author_id: int,
        choices: list[tuple[int, str, int]],
        on_pick: Callable[[discord.Interaction, int], Awaitable[None]],
    ) -> None:
        """
        Args:
            author_id: Discord user id of the slash command invoker.
            choices: list of (author_id, author_name, message_count) tuples
                sorted by message_count desc. Discord caps Select options
                at 25; we slice to the first 25 to stay within the cap.
            on_pick: async callback invoked with (select_interaction,
                picked_author_id). Responsible for responding to the
                interaction — this view does not auto-defer so the caller
                has freedom to edit_original_response or send a followup.
        """
        super().__init__(timeout=120.0)
        self.author_id = author_id
        self.message: discord.Message | None = None
        self.add_item(_ExportUserSelect(choices=choices, on_pick=on_pick))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This is not your export dialog.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Select):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    content="Export selection timed out.", view=self
                )
            except discord.HTTPException:
                pass


class ResetConfirmView(discord.ui.View):
    """Generic destructive-action confirmation view.

    Used by `/pb config reset` (wipes guild_config fields) and
    `/pb db reset` (wipes all scraped data for the guild). Author-only,
    30s timeout. On confirm the view runs ``on_reset`` — the callback
    performs the destructive action and returns the outcome message
    text, which the view writes back to the interaction in a single
    edit. Mirrors the callback pattern used by ``ChannelPickerView`` /
    ``UserPickerView``.

    The confirm button is added dynamically (instead of via the usual
    ``@discord.ui.button`` decorator) so its label can vary per caller
    — "Reset Config", "Delete Server Data", etc. The Cancel button is
    decorator-driven since its label is fixed.
    """

    def __init__(
        self,
        author_id: int,
        on_reset: Callable[[], Awaitable[str]],
        *,
        button_label: str,
    ) -> None:
        super().__init__(timeout=30.0)
        self.author_id = author_id
        self.on_reset = on_reset
        self.message: discord.Message | None = None

        confirm_button: discord.ui.Button = discord.ui.Button(
            label=button_label, style=discord.ButtonStyle.danger
        )
        confirm_button.callback = self._confirm  # type: ignore[method-assign]
        self.add_item(confirm_button)

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

    async def _confirm(self, interaction: discord.Interaction) -> None:
        self._disable_all()
        outcome = await self.on_reset()
        self.stop()
        await interaction.response.edit_message(content=outcome, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(content="Reset cancelled.", view=None)
