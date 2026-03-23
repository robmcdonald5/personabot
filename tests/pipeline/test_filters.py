"""Tests for quality filters."""

from factories import make_message

from personabot.pipeline.filters import (
    is_long_enough,
    is_not_bot_command,
    is_not_emoji_only,
    is_not_link_only,
    is_not_low_effort,
    passes_all_filters,
)
from personabot.schemas.discord import DiscordMessage


def _msg(content: str, word_count: int | None = None) -> DiscordMessage:
    """make_message with content-first signature for filter tests."""
    wc = word_count if word_count is not None else len(content.split())
    return make_message(content=content, word_count=wc)


# --- is_long_enough ---


def test_short_message_filtered():
    assert is_long_enough(_msg("hi", 1)) is False
    assert is_long_enough(_msg("ok then", 2)) is False


def test_adequate_length_kept():
    assert is_long_enough(_msg("this is fine", 3)) is True
    assert is_long_enough(_msg("a much longer message with many words", 7)) is True


# --- is_not_emoji_only ---


def test_emoji_only_filtered():
    assert is_not_emoji_only(_msg("\U0001f600\U0001f600\U0001f600")) is False
    assert is_not_emoji_only(_msg("\U0001f600 \U0001f602")) is False


def test_emoji_with_text_kept():
    assert is_not_emoji_only(_msg("haha \U0001f600 good one")) is True


# --- is_not_low_effort ---


def test_low_effort_filtered():
    assert is_not_low_effort(_msg("lol")) is False
    assert is_not_low_effort(_msg("LMAO")) is False
    assert is_not_low_effort(_msg("  bruh  ")) is False
    assert is_not_low_effort(_msg("nice")) is False


def test_normal_message_kept():
    assert is_not_low_effort(_msg("that was a nice play though")) is True


# --- is_not_link_only ---


def test_link_only_filtered():
    assert is_not_link_only(_msg("https://example.com")) is False
    assert is_not_link_only(_msg("https://a.com https://b.com")) is False


def test_link_with_text_kept():
    assert is_not_link_only(_msg("check this out https://example.com")) is True


# --- is_not_bot_command ---


def test_bot_commands_filtered():
    assert is_not_bot_command(_msg("!play song")) is False
    assert is_not_bot_command(_msg("/roll 20")) is False
    assert is_not_bot_command(_msg("$balance")) is False


def test_normal_text_kept():
    assert is_not_bot_command(_msg("I think that's a good idea")) is True


def test_question_mark_and_dot_not_filtered():
    """Messages starting with ? or . should not be rejected as bot commands."""
    assert is_not_bot_command(_msg("? anyone know the answer")) is True
    assert is_not_bot_command(_msg("...so what happened")) is True
    assert is_not_bot_command(_msg(".maybe this is fine")) is True


# --- passes_all_filters ---


def test_quality_message_passes_all():
    msg = _msg("I think the new update is really interesting, lots of changes", 10)
    assert passes_all_filters(msg) is True


def test_short_low_effort_fails():
    msg = _msg("lol", 1)
    assert passes_all_filters(msg) is False
