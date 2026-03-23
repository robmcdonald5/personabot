"""Tests for weighted scoring."""

from factories import make_message

from personabot.pipeline.scoring import calculate_score, score_and_rank


def test_baseline_score():
    """Message with no special signals gets a low score."""
    scored = calculate_score(make_message())
    assert 0.0 <= scored.score <= 1.0
    assert scored.score < 0.3


def test_reactions_boost_score():
    low = calculate_score(make_message(reaction_count=0))
    high = calculate_score(make_message(reaction_count=10))
    assert high.score > low.score


def test_pinned_boosts_score():
    unpinned = calculate_score(make_message(is_pinned=False))
    pinned = calculate_score(make_message(is_pinned=True))
    assert pinned.score > unpinned.score


def test_reply_thread_boosts_score():
    plain = calculate_score(make_message())
    reply = calculate_score(make_message(reply_to_id=999))
    assert reply.score > plain.score


def test_formatting_boosts_score():
    plain = calculate_score(make_message(content="just some text here today"))
    formatted = calculate_score(
        make_message(content="**bold text** and `code` and more words")
    )
    assert formatted.score > plain.score


def test_engagement_boosts_score():
    """Messages that received replies should score higher."""
    msg = make_message(message_id=1)
    no_engagement = calculate_score(msg, received_replies=set())
    with_engagement = calculate_score(msg, received_replies={1})
    assert with_engagement.score > no_engagement.score


def test_score_breakdown_keys():
    scored = calculate_score(make_message())
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
        make_message(message_id=1, content="lol", word_count=1),  # Filtered out
        make_message(
            message_id=2,
            content="A decent quality message here",
            word_count=5,
            reaction_count=5,
        ),
        make_message(
            message_id=3, content="Another message with some text", word_count=5
        ),
    ]
    ranked = score_and_rank(messages, top_n=10)
    assert len(ranked) == 2  # "lol" filtered
    assert ranked[0].score >= ranked[1].score


def test_score_and_rank_top_n():
    messages = [
        make_message(
            message_id=i,
            content=f"Message number {i} with content",
            word_count=5,
            reaction_count=i,
        )
        for i in range(20)
    ]
    ranked = score_and_rank(messages, top_n=5)
    assert len(ranked) == 5
