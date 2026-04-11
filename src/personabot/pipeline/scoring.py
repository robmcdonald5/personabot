"""Weighted scoring for message quality. Pure functions."""

import heapq
import re

from personabot.pipeline.filters import passes_all_filters
from personabot.schemas.discord import DiscordMessage, ScoredMessage

# Weight constants from architecture spec
WEIGHT_REACTIONS = 0.30
WEIGHT_LENGTH = 0.20
WEIGHT_REPLY_THREAD = 0.15
WEIGHT_PINNED = 0.15
WEIGHT_ENGAGEMENT = 0.10
WEIGHT_FORMATTING = 0.10

# Formatting patterns (markdown, code blocks, lists)
_FORMATTING_PATTERN = re.compile(
    r"```|`[^`]+`|\*\*|__|\*|_|- |\d+\. |^> ",
    re.MULTILINE,
)


def _reaction_signal(msg: DiscordMessage) -> float:
    return min(msg.reaction_count / 10, 1.0)


def _length_signal(msg: DiscordMessage) -> float:
    return min(msg.word_count / 100, 1.0)


def _reply_thread_signal(msg: DiscordMessage) -> float:
    return 1.0 if (msg.reply_to_id is not None or msg.thread_id is not None) else 0.0


def _pinned_signal(msg: DiscordMessage) -> float:
    return 1.0 if msg.is_pinned else 0.0


def _formatting_signal(msg: DiscordMessage) -> float:
    return 1.0 if _FORMATTING_PATTERN.search(msg.content) else 0.0


def calculate_score(
    msg: DiscordMessage, received_replies: set[int] | None = None
) -> ScoredMessage:
    """Calculate composite quality score for a single message."""
    engagement = (
        1.0 if (received_replies and msg.message_id in received_replies) else 0.0
    )

    breakdown = {
        "reactions": _reaction_signal(msg) * WEIGHT_REACTIONS,
        "length": _length_signal(msg) * WEIGHT_LENGTH,
        "reply_thread": _reply_thread_signal(msg) * WEIGHT_REPLY_THREAD,
        "pinned": _pinned_signal(msg) * WEIGHT_PINNED,
        "engagement": engagement * WEIGHT_ENGAGEMENT,
        "formatting": _formatting_signal(msg) * WEIGHT_FORMATTING,
    }
    total = sum(breakdown.values())

    # model_construct skips re-validation — data comes from an already-validated
    # DiscordMessage. model_dump() is the public, forward-compatible way to
    # extract field data (safer than reaching into msg.__dict__).
    return ScoredMessage.model_construct(
        **msg.model_dump(),
        score=round(total, 4),
        score_breakdown=breakdown,
    )


def score_and_rank(
    messages: list[DiscordMessage], top_n: int = 1000
) -> list[ScoredMessage]:
    """Filter, score, and rank messages. Returns top N by score descending."""
    received_replies = {
        msg.reply_to_id for msg in messages if msg.reply_to_id is not None
    }

    # Generator feeds heapq.nlargest so memory stays at O(top_n) rather than O(N)
    # even for users with tens of thousands of messages.
    return heapq.nlargest(
        top_n,
        (
            calculate_score(msg, received_replies)
            for msg in messages
            if passes_all_filters(msg)
        ),
        key=lambda m: m.score,
    )
