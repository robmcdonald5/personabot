"""Tests for conversation windowing."""

from personabot.pipeline.windowing import create_windows, inject_reply_context
from personabot.schemas.discord import DiscordMessage, ScoredMessage


def _scored(**overrides) -> ScoredMessage:
    defaults = dict(
        message_id=1,
        guild_id=1,
        channel_id=100,
        channel_name="test-channel",
        author_id=1,
        author_name="test",
        content="Test message content here",
        timestamp="2026-01-01T12:00:00Z",
        word_count=4,
        score=0.5,
        score_breakdown={},
    )
    defaults.update(overrides)
    return ScoredMessage(**defaults)


def test_single_message_one_window():
    windows = create_windows([_scored()])
    assert len(windows) == 1
    assert len(windows[0].messages) == 1
    assert windows[0].messages[0].message_id == 1


def test_close_messages_same_window():
    """Messages within the gap threshold stay in one window."""
    messages = [
        _scored(message_id=1, timestamp="2026-01-01T12:00:00Z"),
        _scored(message_id=2, timestamp="2026-01-01T12:30:00Z"),
        _scored(message_id=3, timestamp="2026-01-01T13:00:00Z"),
    ]
    windows = create_windows(messages, gap_hours=4)
    assert len(windows) == 1
    assert len(windows[0].messages) == 3


def test_gap_creates_new_window():
    """A gap > threshold starts a new window."""
    messages = [
        _scored(message_id=1, timestamp="2026-01-01T08:00:00Z"),
        _scored(message_id=2, timestamp="2026-01-01T08:30:00Z"),
        _scored(message_id=3, timestamp="2026-01-01T14:00:00Z"),  # 5.5h gap
    ]
    windows = create_windows(messages, gap_hours=4)
    assert len(windows) == 2
    assert len(windows[0].messages) == 2
    assert len(windows[1].messages) == 1


def test_channel_change_creates_new_window():
    """Different channels create separate windows."""
    messages = [
        _scored(message_id=1, channel_id=100, timestamp="2026-01-01T12:00:00Z"),
        _scored(message_id=2, channel_id=200, timestamp="2026-01-01T12:05:00Z"),
    ]
    windows = create_windows(messages, gap_hours=4)
    assert len(windows) == 2


def test_empty_input():
    windows = create_windows([])
    assert windows == []


def test_window_quality_score_is_mean():
    messages = [
        _scored(message_id=1, score=0.8, timestamp="2026-01-01T12:00:00Z"),
        _scored(message_id=2, score=0.4, timestamp="2026-01-01T12:10:00Z"),
    ]
    windows = create_windows(messages)
    assert windows[0].quality_score == 0.6


def test_window_ids_sequential():
    messages = [
        _scored(message_id=1, timestamp="2026-01-01T08:00:00Z"),
        _scored(message_id=2, timestamp="2026-01-01T20:00:00Z"),
        _scored(message_id=3, timestamp="2026-01-02T10:00:00Z"),
    ]
    windows = create_windows(messages, gap_hours=4)
    assert [w.window_id for w in windows] == ["w_001", "w_002", "w_003"]


def test_target_author_id_sets_is_target_user():
    messages = [
        _scored(message_id=1, author_id=100, timestamp="2026-01-01T12:00:00Z"),
        _scored(message_id=2, author_id=200, timestamp="2026-01-01T12:05:00Z"),
    ]
    windows = create_windows(messages, target_author_id=100)
    assert windows[0].messages[0].is_target_user is True
    assert windows[0].messages[1].is_target_user is False


def test_inject_reply_context():
    """Reply context is populated from the all_messages dict."""
    messages = [
        _scored(
            message_id=2,
            reply_to_id=1,
            timestamp="2026-01-01T12:05:00Z",
        ),
    ]
    windows = create_windows(messages)

    parent = DiscordMessage(
        message_id=1,
        guild_id=1,
        channel_id=100,
        channel_name="test-channel",
        author_id=99,
        author_name="parent_user",
        content="This is the parent message",
        timestamp="2026-01-01T12:00:00Z",
        word_count=5,
    )
    all_msgs = {1: parent}
    inject_reply_context(windows, all_msgs)

    assert windows[0].messages[0].reply_context == "This is the parent message"


def test_inject_reply_context_missing_parent():
    """Missing parent leaves reply_context as None."""
    messages = [
        _scored(message_id=2, reply_to_id=999, timestamp="2026-01-01T12:05:00Z"),
    ]
    windows = create_windows(messages)
    inject_reply_context(windows, {})

    assert windows[0].messages[0].reply_context is None
