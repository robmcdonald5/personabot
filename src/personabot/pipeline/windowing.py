"""Conversation windowing — groups messages by temporal proximity."""

from datetime import datetime, timedelta

from personabot.schemas.corpus import ConversationWindow, WindowMessage
from personabot.schemas.discord import DiscordMessage, ScoredMessage


def create_windows(
    messages: list[ScoredMessage],
    gap_hours: int = 4,
    target_author_id: int | None = None,
) -> list[ConversationWindow]:
    """Group messages into conversation windows.

    A new window starts when the gap between consecutive messages
    exceeds gap_hours or the channel changes.
    """
    if not messages:
        return []

    # Sort by timestamp
    sorted_msgs = sorted(messages, key=lambda m: m.timestamp)
    gap = timedelta(hours=gap_hours)

    windows: list[ConversationWindow] = []
    current_msgs: list[ScoredMessage] = [sorted_msgs[0]]
    prev_time = datetime.fromisoformat(sorted_msgs[0].timestamp)

    for msg in sorted_msgs[1:]:
        curr_time = datetime.fromisoformat(msg.timestamp)

        if (curr_time - prev_time) > gap or msg.channel_id != current_msgs[
            -1
        ].channel_id:
            # Close current window and start new one
            windows.append(_build_window(current_msgs, len(windows), target_author_id))
            current_msgs = [msg]
        else:
            current_msgs.append(msg)
        prev_time = curr_time

    # Close final window
    windows.append(_build_window(current_msgs, len(windows), target_author_id))
    return windows


def _build_window(
    messages: list[ScoredMessage],
    index: int,
    target_author_id: int | None = None,
) -> ConversationWindow:
    """Build a ConversationWindow from a group of messages."""
    participants = list({m.author_name for m in messages})
    quality_score = sum(m.score for m in messages) / len(messages) if messages else 0.0

    window_messages = [
        WindowMessage(
            message_id=m.message_id,
            content=m.content,
            timestamp=m.timestamp,
            author_name=m.author_name,
            is_target_user=(
                m.author_id == target_author_id
                if target_author_id is not None
                else True
            ),
            reaction_count=m.reaction_count,
            is_reply=m.reply_to_id is not None,
            reply_to_id=m.reply_to_id,
            reply_context=None,
            score=m.score,
        )
        for m in messages
    ]

    return ConversationWindow(
        window_id=f"w_{index + 1:03d}",
        channel_name=messages[0].channel_name or str(messages[0].channel_id),
        channel_id=messages[0].channel_id,
        start_time=messages[0].timestamp,
        end_time=messages[-1].timestamp,
        participants=participants,
        messages=window_messages,
        quality_score=round(quality_score, 4),
    )


def inject_reply_context(
    windows: list[ConversationWindow],
    all_messages: dict[int, DiscordMessage],
) -> None:
    """Look up parent messages for replies and set reply_context in place."""
    for window in windows:
        for wm in window.messages:
            if not wm.is_reply or wm.reply_to_id is None:
                continue
            parent = all_messages.get(wm.reply_to_id)
            if parent is not None:
                wm.reply_context = parent.content[:200]
