"""Shared model factories for tests."""

from typing import Any

from personabot.schemas.discord import DiscordMessage, ScoredMessage


def make_message(**overrides: Any) -> DiscordMessage:
    """Create a DiscordMessage with sensible defaults."""
    defaults: dict[str, Any] = dict(
        message_id=1,
        guild_id=1,
        channel_id=1,
        channel_name="test-channel",
        author_id=1,
        author_name="test",
        content="This is a test message with enough words",
        timestamp="2026-01-01T12:00:00Z",
        reaction_count=0,
        word_count=8,
    )
    defaults.update(overrides)
    return DiscordMessage(**defaults)


def make_scored(**overrides: Any) -> ScoredMessage:
    """Create a ScoredMessage with sensible defaults."""
    score = overrides.pop("score", 0.5)
    breakdown = overrides.pop("score_breakdown", {})
    base = make_message(**overrides)
    return ScoredMessage(**base.model_dump(), score=score, score_breakdown=breakdown)
