"""Tests for weighted scoring."""

from personabot.pipeline.scoring import calculate_score, score_and_rank
from personabot.schemas.discord import DiscordMessage


def _msg(**overrides) -> DiscordMessage:
    defaults = dict(
        message_id=1,
        guild_id=1,
        channel_id=1,
        channel_name="test-channel",
        author_id=1,
        author_name="test",
        content="This is a normal test message with words",
        timestamp="2026-01-01T00:00:00Z",
        word_count=8,
    )
    defaults.update(overrides)
    return DiscordMessage(**defaults)


def test_baseline_score():
    """Message with no special signals gets a low score."""
    scored = calculate_score(_msg())
    assert 0.0 <= scored.score <= 1.0
    assert scored.score < 0.3


def test_reactions_boost_score():
    low = calculate_score(_msg(reaction_count=0))
    high = calculate_score(_msg(reaction_count=10))
    assert high.score > low.score


def test_pinned_boosts_score():
    unpinned = calculate_score(_msg(is_pinned=False))
    pinned = calculate_score(_msg(is_pinned=True))
    assert pinned.score > unpinned.score


def test_reply_thread_boosts_score():
    plain = calculate_score(_msg())
    reply = calculate_score(_msg(reply_to_id=999))
    assert reply.score > plain.score


def test_formatting_boosts_score():
    plain = calculate_score(_msg(content="just some text here today"))
    formatted = calculate_score(_msg(content="**bold text** and `code` and more words"))
    assert formatted.score > plain.score


def test_engagement_boosts_score():
    """Messages that received replies should score higher."""
    msg = _msg(message_id=1)
    no_engagement = calculate_score(msg, received_replies=set())
    with_engagement = calculate_score(msg, received_replies={1})
    assert with_engagement.score > no_engagement.score


def test_score_breakdown_keys():
    scored = calculate_score(_msg())
    expected_keys = {
        "reactions",
        "length",
        "reply_thread",
        "pinned",
        "engagement",
        "formatting",
    }
    assert set(scored.score_breakdown.keys()) == expected_keys


def test_score_and_rank_filters_and_sorts():
    messages = [
        _msg(message_id=1, content="lol", word_count=1),  # Filtered out
        _msg(
            message_id=2,
            content="A decent quality message here",
            word_count=5,
            reaction_count=5,
        ),
        _msg(message_id=3, content="Another message with some text", word_count=5),
    ]
    ranked = score_and_rank(messages, top_n=10)
    assert len(ranked) == 2  # "lol" filtered
    assert ranked[0].score >= ranked[1].score


def test_score_and_rank_top_n():
    messages = [
        _msg(
            message_id=i,
            content=f"Message number {i} with content",
            word_count=5,
            reaction_count=i,
        )
        for i in range(20)
    ]
    ranked = score_and_rank(messages, top_n=5)
    assert len(ranked) == 5
